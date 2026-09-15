# Grandice Privacy Shield

Grandice Privacy Shield protects structured requests before they reach a model.
One policy model powers the Python SDK, TypeScript SDK, local sidecar, and hosted
privacy API.

## Guarantees and boundaries

| Mode | Where original content is processed | Promise |
| --- | --- | --- |
| Python or TypeScript SDK | Application process | Zero transfer to Grandice |
| Local sidecar | Customer-controlled machine or network | Zero transfer to Grandice |
| Hosted privacy | Grandice gateway memory | Zero retention by the privacy layer |

TLS protects data in transit but does not make hosted processing zero-transfer.
The hosted service necessarily receives the original request.

The gateway's usage database stores request metadata and token counts, not
prompt or completion bodies. A production zero-retention deployment must also
disable payload capture in reverse proxies, observability tools, crash dumps,
and support tooling.

## Current implementation status

The first implementation provides:

- Recursive text and JSON inspection
- Pattern-based detection for common personal, financial, legal, and secret data
- Irreversible masking
- Reversible local tokenization
- Privacy receipts that exclude sensitive values
- Authenticated hosted detect, redact, tokenize, policy, and capability routes
- Local Python sync and async clients
- A local OpenAI-compatible JSON sidecar
- A TypeScript/Node/browser privacy engine and fetch wrapper
- Fail-closed local image, audio, and video processing pipelines

The multimedia processor removes metadata, redacts OCR-matched text, faces and
codes in images, mutes locally transcribed sensitive audio intervals, and
processes every video frame plus its audio track. It uses only local tools and
commits output atomically.

This installation includes Tesseract, FFmpeg, FFprobe, OpenCV visual and barcode
detectors, and a local faster-whisper large-v3-turbo model. Synthetic image,
audio, and video pipelines have been exercised locally. This does not make the
detectors production-validated; representative domain evaluation data and
human review are still required.

Pattern detection is only a baseline. It cannot reliably identify every person,
address, confidential fact, face, voice, or context-dependent identifier.
Strict deployments must add local named-entity and multimedia detectors and
measure them against representative evaluation data.

Install the complete local multimedia runtime with:

```powershell
.\scripts\install-privacy-media.ps1
```

The script installs Tesseract, FFmpeg/FFprobe, the OpenCV visual and barcode
runtime, and a local faster-whisper model. Tesseract, FFmpeg, FFprobe, and the
selected Whisper model must remain local.
The processor refuses to download a model while handling a request. This
installation defaults to
`~/.grandice/models/faster-whisper-large-v3-turbo` and CPU/int8 inference.
Set `GRANDICE_WHISPER_DEVICE=cuda` and
`GRANDICE_WHISPER_COMPUTE_TYPE=float16` only after installing a compatible
local CUDA runtime.

`POST /v1/privacy/media/redact` accepts `content_base64`, `media_type`, and
`policy`. It currently processes media only on the zero-transfer sidecar. The
hosted route refuses the request before decoding it because the current
processor uses temporary files and therefore cannot satisfy the no-save
contract. The sidecar automatically uses the default model path; set
`GRANDICE_PRIVACY_WHISPER_MODEL` to override it with another existing local
speech-model directory.

## Hosted privacy API

All routes require a normal Grandice API key.

```text
GET  /v1/privacy/capabilities
GET  /v1/privacy/policies
GET  /v1/privacy/policies/{policy_id}
POST /v1/privacy/detect
POST /v1/privacy/redact
POST /v1/privacy/tokenize
POST /v1/privacy/media/redact
```

Example:

```powershell
$body = @{
    policy = "financial-strict-v1"
    data = @{
        prompt = "Email alice@example.com about account 87432291."
    }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8080/v1/privacy/redact" `
    -Headers @{ Authorization = "Bearer gll-..." } `
    -ContentType "application/json" `
    -Body $body
```

Hosted protection can also be applied to the existing model endpoints without a
separate redaction call:

```text
X-Grandice-Privacy-Mode: hosted
X-Grandice-Privacy-Policy: financial-strict-v1
```

Hosted streaming is currently rejected because a token can span stream chunks.
Returning an unverified stream would violate the privacy contract.

## Python SDK

Install from this repository:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

Use the local wrapper:

```python
from grandice_privacy import GrandicePrivacyClient

with GrandicePrivacyClient(
    base_url="https://llm.grand-ice.com/v1",
    api_key="gll-...",
    policy="financial-strict-v1",
) as client:
    response = client.post(
        "/chat/completions",
        json={
            "model": "chat",
            "messages": [
                {
                    "role": "user",
                    "content": "Email alice@example.com about account 87432291.",
                }
            ],
        },
    )
    print(response.json())
```

The token vault is in memory and is never sent to Grandice. Create a separate
client or vault for each isolation boundary. Do not share one vault between
unrelated users or tenants.

Automatic response rehydration is restricted to user-visible `content`, `text`,
and `output_text` fields. Tokens in tool calls, function arguments, control
fields, or arbitrary structured fields remain protected to prevent a model from
causing secrets to be restored into an executable tool request.

## Local sidecar

Start the sidecar:

```powershell
.\scripts\start-privacy-sidecar.ps1 `
    -Upstream "https://llm.grand-ice.com" `
    -Policy "financial-strict-v1"
```

Then change an existing OpenAI-compatible application's base URL:

```text
http://127.0.0.1:8090/v1
```

The application's existing authorization header passes through to Grandice.
The sidecar binds to loopback by default. Do not expose it to a network without
adding client authentication and TLS.

Zero-transfer sidecar requests must put inputs in JSON values. Query parameters
are refused, sensitive JSON property names are rejected, and only authorization
and content-negotiation headers are forwarded. Remote image/audio/video URLs
are refused because the sidecar cannot sanitize content it does not possess.
Embedded data-URL media is sanitized locally when the complete multimedia
runtime is available.

## TypeScript SDK

```powershell
cd packages\privacy-typescript
npm install
npm run build
```

```typescript
import { createPrivacyFetch } from "@grandice/privacy";

const privacyFetch = createPrivacyFetch({
  policy: "financial-strict-v1",
});

const response = await privacyFetch(
  "https://llm.grand-ice.com/v1/chat/completions",
  {
    method: "POST",
    headers: {
      authorization: "Bearer gll-...",
    },
    body: JSON.stringify({
      model: "chat",
      messages: [{ role: "user", content: "Email alice@example.com." }],
    }),
  },
);
```

The TypeScript engine is intentionally dependency-light. Full local OCR,
audio, and video processing should use the local sidecar rather than transferring
raw media to a hosted detector.

The SDK refuses unsupported or binary fetch body types instead of sending them
unchanged. Request objects containing JSON bodies are inspected locally.

## Policies

Built-in policy identifiers:

- `general-v1`
- `strict-v1`
- `financial-strict-v1`
- `legal-strict-v1`

Policy and detector versions are part of the privacy receipt. Future detector
changes must be released with conformance fixtures and regression evaluations.

