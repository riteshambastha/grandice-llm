# Grandice Domain APIs

Grandice domain APIs are typed, source-attributed workflows. They separate
deterministic calculations from future model-assisted interpretation and always
return assumptions, limitations, warnings, a methodology version, and a
professional-review requirement.

## Initial financial release

```text
POST /v1/financial/portfolio/analyze
POST /v1/financial/risk-assessment
POST /v1/financial/company/analyze
GET  /v1/agents
POST /v1/agents/runs
```

The initial financial workflows are deterministic:

- Portfolio analysis calculates weights, allocation, concentration, HHI,
  effective positions, and transparent diversification flags.
- Risk assessment uses a disclosed weighted score over bounded,
  non-identifying dimensions.
- Company analysis calculates historical growth, margins, net debt, leverage,
  and supported valuation multiples from at least two cited periods.

These APIs do not issue personalized investment recommendations or replace a
licensed professional.

## Input contract

- Monetary values must be JSON decimal strings or integers. JSON floats are
  rejected to avoid hidden binary rounding.
- Requests are capped at 2 MiB, 14 JSON levels, and 25,000 JSON values;
  domain collections and exact-decimal magnitude/scale are separately bounded.
- Every fact-bearing input references caller-provided source metadata.
- Sources and analyses carry explicit `as_of` dates.
- Extra fields, duplicate source identifiers, unknown source references, and
  future-dated periods are rejected.
- Financial identity fields and free-form personal profiles are not accepted.

## Privacy

Use `X-Grandice-Privacy-Mode: client` or `sidecar` after local tokenization for
the strongest guarantee. After authentication, quota enforcement, and request
size checks, raw domain JSON receives a server-side pattern-based residual check
before schema validation. Values detected by `financial-strict-v1` are rejected
before calculations run.

The residual result reports best-effort coverage of configured detectors. It is
not proof that the request contains no personal data: unsupported categories,
names, and context-dependent identifiers may not be detected. Domain schemas
therefore minimize free text and require opaque identifiers where possible.

Domain audit records contain request ID, route, agent ID, privacy mode, status,
latency, schema names, source count, warning count, and methodology version.
Request and response bodies are not stored in domain audit records.

## Managed agents

The first registered agents are:

```text
financial.portfolio-analyst.v1
financial.risk-assessment.v1
financial.company-analyst.v1
```

These are deliberately constrained agents: each dispatches one strict input
schema to declared deterministic tools. The runtime does not accept an
unrestricted objective, browse the web, trade, modify accounts, or execute
model-generated tool arguments.

Future legal and market-research agents will use the same registry and audit
envelope after domain-specific source contracts, policies, approval gates, and
evaluation suites are implemented.

