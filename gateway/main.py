import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import backup, db, monitoring, upstream
from .auth import ApiKey, require_api_key
from .config import get_settings, load_aliases
from .routes import (
    admin,
    agents,
    contact,
    domain,
    financial,
    legal,
    market,
    openai,
    privacy,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s"
)
log = logging.getLogger("gateway")
WEBSITE_DIR = Path(__file__).resolve().parent.parent / "website"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    monitoring.startup()
    await upstream.startup()
    backup.start_scheduler()
    settings = get_settings()
    log.info("Gateway ready on %s:%s -> %s", settings.gateway_host, settings.gateway_port, settings.ollama_base_url)
    if not settings.admin_token:
        log.warning("ADMIN_TOKEN is empty; /admin endpoints are disabled.")
    yield
    await backup.stop_scheduler()
    await upstream.shutdown()


app = FastAPI(
    title="Grandice AI Gateway and Domain Platform",
    version="1.1.0",
    description=(
        "Authenticated access to local models, Privacy Shield, and auditable "
        "source-attributed domain workflows."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.mount("/assets", StaticFiles(directory=WEBSITE_DIR / "assets"), name="website-assets")
app.include_router(openai.router)
app.include_router(privacy.router)
app.include_router(financial.router)
app.include_router(legal.router)
app.include_router(market.router)
app.include_router(agents.router)
app.include_router(domain.router)
app.include_router(admin.router)
app.include_router(contact.router)


@app.get("/", include_in_schema=False)
async def website_home() -> FileResponse:
    return FileResponse(WEBSITE_DIR / "index.html")


@app.get("/api-docs", include_in_schema=False)
async def api_documentation() -> FileResponse:
    return FileResponse(WEBSITE_DIR / "api-docs.html")


@app.get("/financial-advisor", include_in_schema=False)
async def financial_advisor_white_paper() -> FileResponse:
    return FileResponse(WEBSITE_DIR / "financial-advisor.html")


@app.get("/legal-contracts", include_in_schema=False)
async def legal_contract_intelligence_white_paper() -> FileResponse:
    return FileResponse(WEBSITE_DIR / "legal-contracts.html")


@app.get("/market-research", include_in_schema=False)
async def market_research_intelligence_white_paper() -> FileResponse:
    return FileResponse(WEBSITE_DIR / "market-research.html")


@app.get("/contact", include_in_schema=False)
async def contact_page() -> HTMLResponse:
    page = (WEBSITE_DIR / "contact.html").read_text(encoding="utf-8")
    page = page.replace(
        "{{TURNSTILE_SITE_KEY}}", get_settings().turnstile_site_key
    )
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/contact/thanks", include_in_schema=False)
async def contact_thanks() -> FileResponse:
    return FileResponse(WEBSITE_DIR / "contact-thanks.html")


@app.get("/admin/dashboard", include_in_schema=False)
async def admin_dashboard() -> FileResponse:
    return FileResponse(
        WEBSITE_DIR / "admin-dashboard.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/health", tags=["meta"])
async def health() -> dict[str, Any]:
    """Liveness probe. Deliberately unauthenticated and free of detail, because
    this endpoint is reachable from the public tunnel; the installed model list
    is available to authenticated callers via GET /v1/models."""
    try:
        resp = await upstream.client().get("/api/tags", timeout=5.0)
        ollama_ok = resp.status_code == 200
    except Exception as exc:
        log.warning("Health check could not reach Ollama: %s", exc)
        ollama_ok = False

    return {"status": "ok" if ollama_ok else "degraded", "ollama_reachable": ollama_ok}


@app.get("/status", tags=["meta"])
async def status(key: ApiKey = Depends(require_api_key)) -> dict[str, Any]:
    """Detailed view of the stack for authenticated operators."""
    models: list[str] = []
    try:
        resp = await upstream.client().get("/api/tags", timeout=5.0)
        if resp.status_code == 200:
            models = sorted(m["name"] for m in resp.json().get("models", []))
    except Exception as exc:
        log.warning("Status check could not reach Ollama: %s", exc)

    return {
        "app_name": key.name,
        "rpm_limit": key.rpm_limit,
        "models_installed": models,
        "aliases": load_aliases(),
    }


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "gateway.main:app",
        host=settings.gateway_host,
        port=settings.gateway_port,
        workers=1,
        log_level="info",
    )


if __name__ == "__main__":
    run()
