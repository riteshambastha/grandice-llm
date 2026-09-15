import argparse
import asyncio
import base64
import gzip
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .config import get_settings

log = logging.getLogger("gateway.backup")

MAGIC = b"GRANDICE-BACKUP-V1\n"
NONCE_BYTES = 12
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
BACKUP_PATTERN = re.compile(
    r"^grandice-gateway-(\d{8}T\d{6}Z)\.db\.gz\.enc$"
)

_backup_lock = Lock()
_scheduler_task: asyncio.Task[None] | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _encryption_key() -> bytes:
    encoded = get_settings().backup_encryption_key.strip()
    if not encoded:
        raise RuntimeError("BACKUP_ENCRYPTION_KEY is not configured.")
    try:
        key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except Exception as exc:
        raise RuntimeError("BACKUP_ENCRYPTION_KEY is not valid base64.") from exc
    if len(key) != 32:
        raise RuntimeError("BACKUP_ENCRYPTION_KEY must decode to exactly 32 bytes.")
    return key


def generate_encryption_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def status() -> dict[str, Any]:
    path = get_settings().backup_status_path
    if not path.exists():
        return {
            "status": "never_run",
            "last_attempt": None,
            "last_success": None,
            "r2_enabled": get_settings().r2_backup_enabled,
        }
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = {"status": "unknown", "last_attempt": None, "last_success": None}
    result["r2_enabled"] = get_settings().r2_backup_enabled
    return result


def _update_status(**values: Any) -> None:
    current = status()
    current.update(values)
    _write_json(get_settings().backup_status_path, current)


def _sqlite_snapshot(source_path: Path, destination_path: Path) -> None:
    source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=30)) as source:
        with closing(sqlite3.connect(destination_path)) as destination:
            source.backup(destination, pages=256)


def _integrity_check(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {result}")


def _encrypt_file(source: Path, destination: Path, key: bytes) -> None:
    nonce = os.urandom(NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    with source.open("rb") as plain, destination.open("wb") as encrypted:
        encrypted.write(MAGIC)
        encrypted.write(nonce)
        while chunk := plain.read(CHUNK_BYTES):
            encrypted.write(encryptor.update(chunk))
        encrypted.write(encryptor.finalize())
        encrypted.write(encryptor.tag)


def _decrypt_file(source: Path, destination: Path, key: bytes) -> None:
    minimum_size = len(MAGIC) + NONCE_BYTES + TAG_BYTES
    if source.stat().st_size < minimum_size:
        raise RuntimeError("Backup file is truncated.")
    with source.open("rb") as encrypted:
        if encrypted.read(len(MAGIC)) != MAGIC:
            raise RuntimeError("Backup format is not recognized.")
        nonce = encrypted.read(NONCE_BYTES)
        encrypted.seek(-TAG_BYTES, os.SEEK_END)
        tag = encrypted.read(TAG_BYTES)
        ciphertext_bytes = source.stat().st_size - minimum_size
        encrypted.seek(len(MAGIC) + NONCE_BYTES)

        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(MAGIC)
        with destination.open("wb") as plain:
            remaining = ciphertext_bytes
            while remaining:
                chunk = encrypted.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    raise RuntimeError("Backup ciphertext is truncated.")
                remaining -= len(chunk)
                plain.write(decryptor.update(chunk))
            plain.write(decryptor.finalize())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_timestamp(name: str) -> datetime | None:
    match = BACKUP_PATTERN.match(Path(name).name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(
        tzinfo=timezone.utc
    )


def _retention_keep_set(names: list[str]) -> set[str]:
    settings = get_settings()
    now = _utc_now()
    daily_cutoff = now - timedelta(days=settings.backup_local_retention_days)
    weekly_cutoff = now - timedelta(weeks=settings.backup_weekly_retention_weeks)
    parsed = sorted(
        (
            (timestamp, name)
            for name in names
            if (timestamp := _backup_timestamp(name)) is not None
        ),
        reverse=True,
    )
    keep: set[str] = set()
    weekly_seen: set[tuple[int, int]] = set()
    for timestamp, name in parsed:
        if timestamp >= daily_cutoff:
            keep.add(name)
        elif timestamp >= weekly_cutoff:
            week = timestamp.isocalendar()[:2]
            if week not in weekly_seen:
                weekly_seen.add(week)
                keep.add(name)
    return keep


def apply_local_retention() -> list[str]:
    backup_dir = get_settings().backup_dir
    files = list(backup_dir.glob("grandice-gateway-*.db.gz.enc"))
    keep = _retention_keep_set([file.name for file in files])
    deleted: list[str] = []
    for file in files:
        if file.name in keep:
            continue
        file.unlink(missing_ok=True)
        file.with_suffix(file.suffix + ".json").unlink(missing_ok=True)
        deleted.append(file.name)
    return deleted


def _r2_client():
    import boto3
    from botocore.config import Config

    settings = get_settings()
    missing = [
        name
        for name, value in (
            ("R2_ACCOUNT_ID", settings.r2_account_id),
            ("R2_ACCESS_KEY_ID", settings.r2_access_key_id),
            ("R2_SECRET_ACCESS_KEY", settings.r2_secret_access_key),
            ("R2_BUCKET", settings.r2_bucket),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing R2 settings: {', '.join(missing)}")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def _r2_key(filename: str) -> str:
    prefix = get_settings().r2_prefix.strip("/")
    return f"{prefix}/{filename}" if prefix else filename


def upload_to_r2(backup_path: Path, metadata_path: Path) -> None:
    client = _r2_client()
    settings = get_settings()
    client.upload_file(
        str(backup_path),
        settings.r2_bucket,
        _r2_key(backup_path.name),
        ExtraArgs={"ContentType": "application/octet-stream"},
    )
    client.upload_file(
        str(metadata_path),
        settings.r2_bucket,
        _r2_key(metadata_path.name),
        ExtraArgs={"ContentType": "application/json"},
    )


def apply_r2_retention() -> list[str]:
    client = _r2_client()
    settings = get_settings()
    prefix = get_settings().r2_prefix.strip("/")
    response = client.list_objects_v2(
        Bucket=settings.r2_bucket,
        Prefix=f"{prefix}/grandice-gateway-" if prefix else "grandice-gateway-",
    )
    keys = [
        item["Key"]
        for item in response.get("Contents", [])
        if item["Key"].endswith(".db.gz.enc")
    ]
    keep_names = _retention_keep_set([Path(key).name for key in keys])
    delete_objects: list[dict[str, str]] = []
    deleted: list[str] = []
    for key in keys:
        if Path(key).name in keep_names:
            continue
        delete_objects.extend(({"Key": key}, {"Key": key + ".json"}))
        deleted.append(key)
    if delete_objects:
        client.delete_objects(
            Bucket=settings.r2_bucket,
            Delete={"Objects": delete_objects, "Quiet": True},
        )
    return deleted


def create_backup() -> dict[str, Any]:
    if not _backup_lock.acquire(blocking=False):
        raise RuntimeError("A database backup is already running.")
    attempted_at = _utc_now().isoformat(timespec="seconds")
    _update_status(status="running", last_attempt=attempted_at, error=None)
    try:
        settings = get_settings()
        source = settings.database_path
        if not source.exists():
            raise RuntimeError(f"Database does not exist: {source}")
        key = _encryption_key()
        backup_dir = settings.backup_dir
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = _utc_now()
        name = f"grandice-gateway-{timestamp:%Y%m%dT%H%M%SZ}.db.gz.enc"
        backup_path = backup_dir / name
        metadata_path = backup_path.with_suffix(backup_path.suffix + ".json")

        with tempfile.TemporaryDirectory(dir=backup_dir) as temp_directory:
            temporary = Path(temp_directory)
            snapshot_path = temporary / "gateway.db"
            compressed_path = temporary / "gateway.db.gz"
            _sqlite_snapshot(source, snapshot_path)
            _integrity_check(snapshot_path)
            with snapshot_path.open("rb") as plain:
                with gzip.open(compressed_path, "wb", compresslevel=6) as compressed:
                    while chunk := plain.read(CHUNK_BYTES):
                        compressed.write(chunk)
            _encrypt_file(compressed_path, backup_path, key)

        metadata: dict[str, Any] = {
            "format": "GRANDICE-BACKUP-V1",
            "created_at": timestamp.isoformat(timespec="seconds"),
            "filename": name,
            "encrypted_bytes": backup_path.stat().st_size,
            "database_bytes": source.stat().st_size,
            "sha256": _sha256(backup_path),
            "integrity_check": "ok",
            "r2_uploaded": False,
            "r2_error": None,
        }
        _write_json(metadata_path, metadata)

        if settings.r2_backup_enabled:
            try:
                upload_to_r2(backup_path, metadata_path)
                metadata["r2_uploaded"] = True
                _write_json(metadata_path, metadata)
                # Re-upload metadata after its remote status is final.
                _r2_client().upload_file(
                    str(metadata_path),
                    settings.r2_bucket,
                    _r2_key(metadata_path.name),
                    ExtraArgs={"ContentType": "application/json"},
                )
                apply_r2_retention()
            except Exception as exc:
                metadata["r2_error"] = str(exc)
                _write_json(metadata_path, metadata)
                log.warning("R2 backup upload failed; local backup is safe: %s", exc)

        apply_local_retention()
        completed_at = _utc_now().isoformat(timespec="seconds")
        final_status = (
            "success"
            if not settings.r2_backup_enabled or metadata["r2_uploaded"]
            else "local_only"
        )
        _update_status(
            status=final_status,
            last_success=completed_at,
            last_backup=name,
            encrypted_bytes=metadata["encrypted_bytes"],
            r2_uploaded=metadata["r2_uploaded"],
            r2_error=metadata["r2_error"],
            error=None,
        )
        return metadata
    except Exception as exc:
        _update_status(status="failed", error=str(exc))
        raise
    finally:
        _backup_lock.release()


def _restore_to_path(backup_path: Path, destination: Path) -> None:
    key = _encryption_key()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp_directory:
        temporary = Path(temp_directory)
        compressed_path = temporary / "gateway.db.gz"
        restored_path = temporary / "gateway.db"
        _decrypt_file(backup_path, compressed_path, key)
        with gzip.open(compressed_path, "rb") as compressed:
            with restored_path.open("wb") as restored:
                while chunk := compressed.read(CHUNK_BYTES):
                    restored.write(chunk)
        _integrity_check(restored_path)
        restored_path.replace(destination)


def verify_backup(backup_path: Path) -> dict[str, Any]:
    if not backup_path.exists():
        raise FileNotFoundError(backup_path)
    with tempfile.TemporaryDirectory() as temp_directory:
        restored_path = Path(temp_directory) / "verified.db"
        _restore_to_path(backup_path, restored_path)
        with closing(sqlite3.connect(restored_path)) as connection:
            table_count = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        return {
            "filename": backup_path.name,
            "integrity_check": "ok",
            "tables": table_count,
            "sha256": _sha256(backup_path),
        }


def restore_backup(
    backup_path: Path, destination: Path, *, overwrite: bool = False
) -> dict[str, Any]:
    if destination.exists() and not overwrite:
        raise FileExistsError(
            f"Destination exists: {destination}. Pass --overwrite only while the gateway is stopped."
        )
    _restore_to_path(backup_path, destination)
    return {
        "filename": backup_path.name,
        "restored_to": str(destination),
        "integrity_check": "ok",
    }


def list_backups(limit: int = 20) -> list[dict[str, Any]]:
    backup_dir = get_settings().backup_dir
    results: list[dict[str, Any]] = []
    for path in sorted(
        backup_dir.glob("grandice-gateway-*.db.gz.enc"), reverse=True
    )[:limit]:
        metadata_path = path.with_suffix(path.suffix + ".json")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            metadata = {
                "filename": path.name,
                "created_at": (
                    _backup_timestamp(path.name) or datetime.fromtimestamp(0, timezone.utc)
                ).isoformat(),
                "encrypted_bytes": path.stat().st_size,
                "r2_uploaded": False,
            }
        results.append(metadata)
    return results


def _backup_due() -> bool:
    current = status()
    last_success = current.get("last_success")
    if not last_success:
        return True
    try:
        completed = datetime.fromisoformat(last_success)
    except ValueError:
        return True
    return _utc_now() - completed >= timedelta(
        hours=get_settings().backup_interval_hours
    )


async def _scheduler() -> None:
    while True:
        try:
            if get_settings().backup_enabled and _backup_due():
                await asyncio.to_thread(create_backup)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("Scheduled database backup failed: %s", exc)
        await asyncio.sleep(3600)


def start_scheduler() -> None:
    global _scheduler_task
    if get_settings().backup_enabled and _scheduler_task is None:
        _scheduler_task = asyncio.create_task(_scheduler())


async def stop_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is None:
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    _scheduler_task = None


def main() -> None:
    parser = argparse.ArgumentParser(description="Grandice encrypted database backups")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create")
    subparsers.add_parser("list")
    verify = subparsers.add_parser("verify")
    verify.add_argument("backup", type=Path)
    restore = subparsers.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("destination", type=Path)
    restore.add_argument("--overwrite", action="store_true")
    subparsers.add_parser("generate-key")
    args = parser.parse_args()

    if args.command == "create":
        result = create_backup()
    elif args.command == "list":
        result = {"backups": list_backups()}
    elif args.command == "verify":
        result = verify_backup(args.backup)
    elif args.command == "restore":
        result = restore_backup(
            args.backup, args.destination, overwrite=args.overwrite
        )
    else:
        result = {"encryption_key": generate_encryption_key()}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
