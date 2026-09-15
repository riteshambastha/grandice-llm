# Grandice LLM

Self-hosted open-weight language models running on this desktop, exposed to your
applications as an authenticated, OpenAI-compatible API.

Nothing leaves the machine unless you start the tunnel, there are no per-token
costs, and any client library that can talk to OpenAI can talk to this.

Grandice is also adding auditable, source-grounded domain workflows exposed
through developer-friendly APIs. The first foundation is
[Grandice Privacy Shield](PRIVACY.md): local-first sensitive-data protection
shared by client SDKs, a local sidecar, and hosted privacy endpoints.
The [Domain APIs](DOMAIN-APIS.md) provide deterministic financial analysis and
constrained managed agents. [Legal Contract Intelligence](LEGAL-APIS.md) adds
caller-source-attributed clause review, bounded playbook checks and directional
contract comparison.

## How it fits together

```
            your applications                    browser
                    │                               │
                    ▼                               ▼
        ┌───────────────────────────┐   ┌──────────────────────┐
        │  Gateway   :8080          │◄──│  Open WebUI   :3000  │
        │  API keys, rate limits,   │   └──────────────────────┘
        │  usage log, /v1/* routes  │
        └─────────────┬─────────────┘
                      ▼
        ┌───────────────────────────┐
        │  Ollama    127.0.0.1:11434│   GPU: RTX 5070, 12 GB
        └───────────────────────────┘
```

Ollama is bound to the loopback interface and has no authentication of its own,
so the gateway is the only component anything else talks to. Open WebUI is
treated as just another API client, which keeps its traffic in the usage log
alongside everything else.

## Models

Applications reference a role rather than a version, so swapping the model
behind a role is an edit to `config/models.json` instead of a change to every
application.

| Alias    | Model              | Size   | Use for                                  |
| -------- | ------------------ | ------ | ---------------------------------------- |
| `chat`   | `qwen3:8b`         | 5.2 GB | General chat and tool calling             |
| `smart`  | `qwen3.5:9b`       | 6.6 GB | Higher-quality chat, vision, tools, code  |
| `fast`   | `qwen3.5:4b`       | 3.4 GB | Low-latency chat and vision               |
| `code`   | `qwen2.5-coder:7b` | 4.7 GB | Code generation, review, completion       |
| `vision` | `qwen2.5vl:7b`     | 6.0 GB | Image understanding, screenshots, OCR     |
| `embed`  | `nomic-embed-text` | 0.3 GB | Embeddings for RAG and semantic search    |

Raw tags such as `qwen3:8b` work too. `GET /v1/models` lists both.

### Reasoning is off by default

`qwen3` is a hybrid reasoning model that, left alone, writes a long internal
monologue before answering. On a two-word reply that measured **136 tokens
versus 5**, plus the latency to generate them.

The gateway therefore sends `reasoning_effort: "none"` for reasoning-capable
models unless the caller specifies its own value. Ask for thinking when a task
genuinely benefits:

```python
client.chat.completions.create(
    model="chat",
    messages=[{"role": "user", "content": "Plan a database migration."}],
    reasoning_effort="medium",   # "none" | "low" | "medium" | "high"
    max_tokens=2000,             # reasoning needs a generous budget
)
```

Give reasoning requests room. A capped budget produces a reply truncated
mid-thought with `finish_reason: "length"` and no usable answer.

Which model families this applies to is set in `config/models.json`.

## Getting started

Everything below is already installed and running on this machine. These steps
are for rebuilding it elsewhere or after a fresh Windows install.

```powershell
winget install Ollama.Ollama
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\bootstrap.ps1          # writes .env, creates the database, issues a key
.\scripts\start-gateway.ps1
```

`bootstrap.ps1` prints an API key once and stores only its hash. If you lose it,
issue another.

## Connecting an application

Point any OpenAI client at the gateway and use your key.

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="gll-...")

resp = client.chat.completions.create(
    model="chat",
    messages=[{"role": "user", "content": "Summarise this changelog."}],
)
print(resp.choices[0].message.content)
```

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://127.0.0.1:8080/v1",
  apiKey: process.env.GRANDICE_API_KEY,
});
```

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer gll-..." \
  -H "Content-Type: application/json" \
  -d '{"model":"chat","messages":[{"role":"user","content":"Hello"}]}'
```

Runnable versions covering streaming, embeddings, vision, tool calling and
structured output are in [`examples/`](examples/).

### Endpoints

| Method   | Path                   | Auth      | Purpose                            |
| -------- | ---------------------- | --------- | ---------------------------------- |
| `GET`    | `/health`              | none      | Liveness probe                     |
| `GET`    | `/status`              | API key   | Installed models, aliases, limits  |
| `GET`    | `/v1/models`           | API key   | Models available to this key       |
| `POST`   | `/v1/chat/completions` | API key   | Chat, streaming, tools, vision     |
| `POST`   | `/v1/completions`      | API key   | Legacy text completion             |
| `POST`   | `/v1/embeddings`       | API key   | Vectors for RAG                    |
| `GET`    | `/v1/privacy/policies` | API key   | Available privacy policies         |
| `POST`   | `/v1/privacy/detect`   | API key   | Detect sensitive-data locations    |
| `POST`   | `/v1/privacy/redact`   | API key   | Irreversibly mask sensitive data   |
| `POST`   | `/v1/privacy/tokenize` | API key   | Ephemerally tokenize sensitive data |
| `POST`   | `/v1/privacy/media/redact` | API key | Locally process image, audio, or video |
| `POST`   | `/v1/financial/portfolio/analyze` | API key | Portfolio allocation and concentration |
| `POST`   | `/v1/financial/risk-assessment` | API key | Transparent bounded risk scoring |
| `POST`   | `/v1/financial/company/analyze` | API key | Historical financial ratio analysis |
| `POST`   | `/v1/legal/contracts/review` | API key | Clause extraction and playbook issue spotting |
| `POST`   | `/v1/legal/contracts/compare` | API key | Directional clause and supported-risk comparison |
| `GET`    | `/v1/agents`           | API key   | List typed managed agents            |
| `POST`   | `/v1/agents/runs`      | API key   | Run a constrained domain workflow    |
| `GET`    | `/v1/domain/runs/{request_id}` | API key | Retrieve body-free domain audit metadata |
| `POST`   | `/admin/keys`          | admin     | Issue a key                        |
| `GET`    | `/admin/keys`          | admin     | List keys                          |
| `DELETE` | `/admin/keys/{id}`     | admin     | Revoke a key                       |
| `GET`    | `/admin/usage`         | admin     | Per-application usage summary      |

Interactive docs: <http://127.0.0.1:8080/docs>

## Managing keys

Give every application its own key. Revoking one then never disturbs the others,
and the usage log attributes traffic per application.

```powershell
.\scripts\new-key.ps1 -Name billing-bot
.\scripts\new-key.ps1 -Name public-demo -RpmLimit 20 -AllowedModels chat,embed
```

`-AllowedModels` restricts a key to specific models, which is worth doing for
anything exposed to untrusted users. Keys default to 120 requests/minute
(`DEFAULT_RPM_LIMIT` in `.env`).

Review usage, reading the admin token from `.env`:

```powershell
$admin = (Select-String -Path .env -Pattern '^ADMIN_TOKEN=(.+)$').Matches.Groups[1].Value
Invoke-RestMethod -Uri http://127.0.0.1:8080/admin/usage `
  -Headers @{ Authorization = "Bearer $admin" } | ConvertTo-Json -Depth 4
```

## Administrator dashboard

Open `https://grand-ice.com/admin/dashboard` and sign in with the
`ADMIN_TOKEN` stored in `.env`. The token is retained only in that browser tab.

Team access is managed in three levels:

1. Create an organization with a license tier, member allowance and shared RPM
   limit. A free client account defaults to five members.
2. Add members with individual roles and RPM limits.
3. Create one or more application keys for each member, optionally restricting
   each key to specific models and a lower application RPM limit.

All three limits are enforced together. A request must fit within its
application-key, member and organization limits. Suspending an organization or
member immediately blocks all keys beneath it. Keys can also be edited, rotated
or revoked from the dashboard.

The Usage view attributes requests, tokens, latency and errors by organization,
member, application and model. Monitoring shows GPU utilization, VRAM, loaded
models, current inference requests and the gateway queue. Existing keys created
before organizations were introduced remain valid and appear as unassigned
legacy keys until replaced.

## Database backups and recovery

The gateway creates an online SQLite snapshot every 24 hours without stopping
API traffic. Each snapshot is integrity-checked, gzip-compressed and encrypted
with AES-256-GCM before it is written to `backups/`. The Monitoring page in the
administrator dashboard shows backup status, history and manual verification.

Create a backup immediately:

```powershell
.\scripts\backup-database.ps1
```

Restore a backup to a separate database for inspection:

```powershell
.\scripts\restore-database.ps1 `
  -BackupPath .\backups\grandice-gateway-YYYYMMDDTHHMMSSZ.db.gz.enc
```

To replace the live database, first stop the gateway and add `-ReplaceLive`.
The script verifies the selected backup and creates a fresh encrypted safety
backup of the current database before replacement.

Backups use daily/weekly retention: all backups from the latest 30 days are
kept, followed by one per week for 12 weeks. Configure these windows with
`BACKUP_LOCAL_RETENTION_DAYS` and `BACKUP_WEEKLY_RETENTION_WEEKS`.

`BACKUP_ENCRYPTION_KEY` in `.env` is required to restore every backup. Keep a
separate secure copy of this key; losing it makes the encrypted backups
unrecoverable. Never commit `.env`.

Optional Cloudflare R2 off-site copies can be enabled with:

```dotenv
R2_BACKUP_ENABLED=true
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=grandice-backups
R2_PREFIX=database
```

Local backups remain successful if R2 is disabled or temporarily unavailable.

## Browser UI

```powershell
.\scripts\start-webui.ps1      # http://127.0.0.1:3000
```

The first account you register becomes the administrator. Open WebUI reaches the
models through the gateway on its own API key, so its usage appears in
`/admin/usage` like any other application.

## Public website

The gateway also serves the corporate website and developer documentation:

- `https://grand-ice.com`
- `https://grand-ice.com/api-docs`
- `https://grand-ice.com/contact`
- `https://grand-ice.com/admin/dashboard` (administrator token required for data)

The website source is in `website/`. The root domain, `www` and the
`llm.grand-ice.com` API hostname all use the same authenticated Cloudflare
Tunnel; Ollama remains accessible only on localhost.

The contact form validates visitors with Cloudflare Turnstile, retains accepted
submissions in the local SQLite database, and relays them to the configured
`CONTACT_RECIPIENT`. The FormSubmit relay requires a one-time confirmation from
that inbox after the first submission.

## Internet access

```powershell
.\scripts\start-tunnel.ps1
```

The configured named tunnel publishes the permanent endpoint:

```text
https://llm.grand-ice.com/v1
```

The URL is also saved to `logs/tunnel-url.txt`. If the private
`.cloudflared/config.yml` is absent, the script falls back to a temporary
`https://….trycloudflare.com` quick tunnel whose hostname changes after a
restart.

Only the gateway is published. Ollama stays on loopback, so every request from
the internet still needs a valid API key. Before exposing anything publicly:

- Issue a separate key per remote consumer, with `-RpmLimit` and `-AllowedModels` set.
- Revoke keys you no longer recognise in `/admin/usage`.
- Keep `ADMIN_TOKEN` off any machine that does not need it.

## Running unattended

From an **elevated** PowerShell session:

```powershell
.\scripts\install-services.ps1
```

This installs the `GrandiceLLM-Supervisor` scheduled task under the Windows
`SYSTEM` account. It starts at Windows boot before user logon, checks Ollama,
the API gateway, Open WebUI, and Cloudflare Tunnel every 15 seconds, and
restarts a component if its process stops. Its activity is recorded in
`logs/supervisor-YYYYMMDD.log`.

If the script is run without elevation, it installs the same supervisor in the
current user's Startup folder instead. That fallback starts only after logon;
run the command again as Administrator to replace it with the boot task.

Windows can start the stack only after the computer itself powers on. For
recovery from a complete electricity outage, enable **Restore on AC Power
Loss**, **AC Back**, or **After Power Failure: Power On** in the computer's
BIOS/UEFI. A UPS is also recommended to avoid abrupt database writes.

Check everything at once:

```powershell
.\scripts\status.ps1
```

## Performance notes

The RTX 5070's 12 GB holds one of these models entirely in VRAM, which is what
keeps generation fast. Settings applied as user environment variables:

| Variable                   | Value   | Reason                                        |
| -------------------------- | ------- | --------------------------------------------- |
| `OLLAMA_HOST`              | `127.0.0.1:11434` | Never listen on the network        |
| `OLLAMA_MODELS`            | `C:\Users\rites\.ollama\models` | `E:` is nearly full   |
| `OLLAMA_KEEP_ALIVE`        | `30m`   | Avoid reloading between requests               |
| `OLLAMA_MAX_LOADED_MODELS` | `2`     | Two 7–8B models fit; a third would spill       |
| `OLLAMA_FLASH_ATTENTION`   | `1`     | Less memory per attention operation            |
| `OLLAMA_KV_CACHE_TYPE`     | `q8_0`  | Quantised KV cache leaves room for context     |
| `OLLAMA_CONTEXT_LENGTH`    | `8192`  | Default window                                 |

The first request to a model pays a cold-load cost of roughly 30–60 seconds
while weights move to VRAM. Subsequent requests are fast until `KEEP_ALIVE`
expires. Switching between two loaded models is cheap; a third forces an evict
and reload.

Model weights live on `C:` because `E:` has under 20 GB free. Only code and
configuration are on `E:`.

## Layout

```
gateway/          FastAPI application
  config.py       settings, aliases, reasoning defaults
  auth.py         key generation and verification
  db.py           SQLite schema and access
  ratelimit.py    per-key sliding window
  upstream.py     proxying to Ollama, usage capture
  routes/         /v1/* and /admin/*
grandice_privacy/ shared privacy engine and Python SDK
grandice_domain/  typed domain schemas, calculations, and agents
privacy_sidecar/  local zero-transfer API proxy
packages/         TypeScript privacy SDK
config/models.json  aliases and reasoning policy
scripts/          bootstrap, start, key management, status
examples/         Python and Node clients
data/             SQLite database (gitignored)
logs/             runtime logs (gitignored)
```

## Troubleshooting

**Empty `content` in a response.** A reasoning model spent the whole token
budget thinking. Raise `max_tokens`, or leave `reasoning_effort` unset to get
the gateway's default of `none`.

**First request takes a minute.** Cold model load. Raise `OLLAMA_KEEP_ALIVE` to
keep weights resident longer.

**A `pull` stops making progress.** Ollama resumes from partial blobs, so
re-running `ollama pull <model>` picks up where it stopped. Avoid piping its
output through `Select-String`, which can block the transfer.

**401 responses.** The key is wrong or revoked. Check `GET /admin/keys`.

**429 responses.** The key exceeded its rate limit; `Retry-After` says how long
to wait. Raise it with `-RpmLimit` on a new key or change `DEFAULT_RPM_LIMIT`.

**Adding a model.** `ollama pull <model>`, then point an alias at it in
`config/models.json`. Aliases are read per request, so no restart is needed.
