# Grandice Market Research Intelligence

Grandice Market Research Intelligence turns caller-provided, dated, structured
evidence into deterministic market and competitive-landscape analysis. The
initial implementation performs no browsing, URL retrieval, model generation,
forecasting, ranking, or recommendation.

## Endpoints

```text
POST /v1/market/research/analyze
POST /v1/market/competitive-landscape/compare
GET  /v1/agents
POST /v1/agents/runs
GET  /v1/domain/runs/{request_id}
```

Registered workflows:

```text
market.evidence-analyst.v1
market.competitive-landscape.v1
```

## Evidence analysis

The analysis workflow accepts a declared research scope, caller-attributed
sources, and structured evidence. It returns:

- exact dated metric observations and interval changes;
- quantized relative percentage changes without inferring CAGR;
- observation-period and unique-source recency summaries;
- declared source-ID concentration and HHI;
- positive, negative, stable, mixed, and neutral signal counts;
- same-period metric disagreements and material contradictions;
- same-date directional contradictions, unambiguous temporal reversals, and
  ambiguous transitions;
- missing subject, metric, source-diversity, and stale-evidence gaps;
- a transparent evidence-coverage score that is explicitly not confidence,
  accuracy, source quality, or probability of truth;
- bounded evidence references with raw-statement and canonical-record digests;
- a manifest digest binding the methodology, research scope, declared sources,
  and evidence records.

Source IDs, citation labels, and dates are supplied by the caller and are not
independently verified.

## Competitive landscape

Competitive analysis compares two to twenty-five declared entities. A row is
comparable only when all of these match:

- metric name and unit;
- value kind and scale;
- ISO currency, when applicable;
- period type, period start, and period end;
- fiscal-calendar identifier, when applicable;
- accounting basis.

Unknown accounting bases, disputed latest values, missing entities, and
misaligned periods are explicitly incomparable. Grandice does not convert
currencies, normalize units, fill missing values, rank entities, or generate
strategic recommendations.

## Input rules

- Decimal values must be JSON strings or integers; floats, NaN, and infinity
  are rejected.
- Currency values require an ISO currency matching the metric unit.
- Percentage values use percentage units: `"12.5"` means `12.5%`, not `0.125`.
- Percentage, ratio, and percentage-point values require scale `ones`.
- Non-instant periods require a start and end with a duration consistent with
  their declared type. Custom periods are positive and bounded.
- Metric period ends cannot be later than the caller-declared observation date.
- Observations and metric dimensions must be declared in the research scope.
- Evidence IDs and source IDs must be unique within their respective registries.
- Caller fields cannot claim verified, trusted, authoritative, or server-fetched
  provenance.

## Evidence integrity

Every displayed evidence binding includes:

```text
evidence_id
source_id
statement_sha256
evidence_sha256
canonicalization_version
provenance = caller_provided
verification_status = unverified
safe_rendering_required = true
render_as = text
```

`evidence_sha256` covers a versioned canonical serialization of the complete
evidence record. Source bindings separately hash caller citation metadata.
`analysis_manifest_sha256` binds the methodology version, research scope,
canonical source bindings, and canonical evidence digests.

These hashes detect record/version mix-ups. They do not authenticate a source or
prove a claim is true.

## Privacy and safe rendering

For confidential research, use the Grandice Python or TypeScript SDK, or the
loopback privacy sidecar, before transfer. The gateway applies a second
`market-strict-v1` residual scan to raw JSON before schema validation.

The response reports the privacy header as caller asserted:

```json
{
  "mode": "sidecar",
  "mode_attested": false,
  "mode_source": "caller_asserted_header"
}
```

Pattern-based residual checks cannot reliably detect trade secrets, confidential
strategy, every organization name, or context-dependent sensitive information.

Evidence titles, statements, excerpts, and citations are untrusted text.
Consumers must render them as text, not active HTML, and must not automatically
navigate or unfurl caller-provided URLs.

## Retention and audit

Market analysis is transient. The domain audit excludes source bodies, evidence,
excerpts, metrics, findings, manifests, and response bodies. It retains
operational metadata such as request ID, authenticated key and available
organization/member references, endpoint, agent, requested privacy mode, status,
duration, schemas, source/warning counts, and methodology version.

Primary domain audit metadata is purged at startup and through hourly
opportunistic cleanup after `domain_audit_retention_days` (90 days by default).
Infrastructure logs, crash reporting, and encrypted backups have separate
deployment-specific lifecycles.

## Bounds

- Gateway body: 2 MiB
- JSON depth: 14
- JSON values: 25,000
- Evidence items: 2,000
- Sources: 100
- Subjects: 100
- Competitive entities: 25
- Metric dimensions: 100
- Statement: 8,000 characters
- Excerpt: 160 characters
- Displayed bindings per row: 8 or fewer
- Latest metrics: 75
- Trends: 100
- Contradictions: 60
- Research gaps: 65
- Direct analysis deadline: 8 seconds
- Competitive deadline: 10 seconds
- Serialized domain result: 2 MiB maximum
- Concurrent market-analysis child processes: 2 per gateway process

Market analyzers run in killable child processes. A deadline terminates the
worker rather than merely stopping the API from waiting.

## Responsibility boundary

The implementation does not:

- verify sources, publishers, citations, dates, or independence;
- browse or retrieve live market information;
- determine truth, causation, completeness, or forecast accuracy;
- normalize incompatible metrics or infer missing values;
- rank vendors, companies, products, markets, or opportunities;
- provide investment, market, commercial, or strategic advice;
- generate price targets, expected returns, or buy/sell/hold language;
- replace analyst, legal, compliance, security, or executive review.

Every output requires professional review appropriate to its intended use.

