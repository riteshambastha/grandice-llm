# Grandice Legal Contract Intelligence

Grandice Legal Contract Intelligence provides deterministic, review-gated
contract issue spotting and comparison. Contract text is treated only as
untrusted data. It is never passed to an LLM, interpreted as instructions, used
to generate tool calls, browsed, or persisted by the legal analysis layer.

## Endpoints

```text
POST /v1/legal/contracts/review
POST /v1/legal/contracts/compare
GET  /v1/agents
POST /v1/agents/runs
GET  /v1/domain/runs/{request_id}
```

Registered workflows:

```text
legal.contract-reviewer.v1
legal.contract-comparator.v1
```

## Contract review

The review workflow:

- segments documents using deterministic heading rules;
- classifies clauses against a fixed commercial-contract taxonomy;
- returns zero-based, end-exclusive Unicode code-point spans and one-based line
  spans;
- binds each clause and finding to document, source, full-document, span, and
  excerpt SHA-256 values;
- applies explainable literal risk rules;
- evaluates a bounded, literal-only caller playbook;
- reports missing required clause categories and configured policy deviations;
- returns assumptions, limitations, warnings, completeness counts, and
  `professional_review_required: true`.

Supported categories include services, scope, service levels, fees, payment,
term, termination, renewal, warranties, liability, indemnity, insurance,
confidentiality, data protection, information security, intellectual property,
license, assignment, subcontracting, compliance, audit, records, governing law,
dispute resolution, force majeure, notices, change control, and standard
boilerplate.

## Contract comparison

Comparison direction is always `original_to_revised`. Clauses are blocked by
typed category, matched by exact span hash where possible, then compared using
bounded token similarity:

- no more than 512 normalized tokens per clause;
- no more than 16 revised candidates per original clause;
- exact, modified, added, removed, unchanged, and ambiguous outcomes;
- explicit match basis and ambiguous-candidate count;
- independently bound original and revised source spans;
- new, resolved, and persistent supported risk identifiers;
- completeness and omitted-count fields when extraction, finding, or change
  caps are reached.

Text similarity is lexical. It does not establish legal or semantic equivalence.

## Playbooks

Inline playbooks are immutable request values and accept only typed bounded
fields:

- required clause categories;
- prohibited case-insensitive literal phrases;
- preferred governing-law label;
- liability-cap requirement;
- bounded review policies over declared clause categories and literal phrases.

Regular expressions, prompts, executable expressions, URLs, imports, tools, and
dynamic field paths are not accepted. A playbook cannot disable mandatory
warnings or professional review.

## Privacy

For confidential contracts, protect values locally with the Grandice Python or
TypeScript SDK, or send the request through the loopback privacy sidecar. The
gateway applies a second `legal-strict-v1` residual policy scan to raw JSON
before legal schema validation. Detected categories are rejected before
analysis.

This is defense in depth. Residual detection covers configured patterns and
does not replace institution-specific privacy evaluation or local data
minimization.

## Retention and audit

Legal analysis and comparison are transient and in memory. The domain audit
table excludes contract text, excerpts, citations, findings, and output bodies.
It retains operational metadata such as request ID, authenticated key and
organization references, endpoint, agent ID, privacy mode, status, duration,
schema names, counts, and methodology version.

Primary domain audit metadata is purged at startup and by an hourly
opportunistic cleanup after the configured `domain_audit_retention_days` period
(90 days by default). Infrastructure logs and encrypted database backups have
separate operational lifecycles and must be reviewed by the deploying
institution.

## Limits

- Gateway body: 2 MiB
- JSON depth: 14
- JSON values: 25,000
- Contract text: 200,000 characters per document
- Extracted clauses: 250 per document
- Findings: 250
- Comparison changes: 250
- Excerpt: 320 characters
- Review policies: 50
- Candidate comparisons: 16 per original clause
- Direct review deadline: 5 seconds
- Direct comparison deadline: 10 seconds

Timeouts stop the API from waiting; deterministic token bounds prevent the
comparison algorithm from continuing with unbounded quadratic work.

## Responsibility boundary

The implementation performs deterministic issue spotting. It does not:

- provide legal advice;
- determine enforceability, validity, compliance, or legal effect;
- verify caller-provided sources;
- replace a lawyer or institutional review process;
- identify every clause, risk, inconsistency, or sensitive value;
- browse law, retrieve legal authority, file documents, sign, negotiate, or
  execute changes.

Every result requires review by qualified legal professionals.

