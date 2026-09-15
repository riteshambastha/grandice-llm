# Grandice Product Direction

Status: Product requirements and architecture direction. No implementation is implied by this document.

## Positioning

Grandice is evolving from an OpenAI-compatible local LLM gateway into a domain intelligence platform.

> Grandice provides auditable, source-grounded domain workflows exposed through developer-friendly APIs.

The platform will retain its general model APIs while adding typed domain APIs and managed domain agents.

## Domain APIs

Initial API direction:

```text
POST /v1/financial/portfolio/analyze
POST /v1/financial/risk-assessment
POST /v1/financial/company/analyze
POST /v1/legal/contracts/review
POST /v1/legal/contracts/compare
POST /v1/research/reports/generate
```

Domain APIs must return structured, validated results with explicit assumptions, warnings, source citations, and freshness information.

## Managed Domain Agents

Initial agent families:

- Financial research analyst
- Portfolio risk analyst
- Investment-policy-statement assistant
- Client meeting preparation agent
- Financial document extraction agent
- Compliance review agent
- Market monitoring agent
- Legal contract review and redline agent
- Market research and report generation agent

Agents are decision-support systems. Consequential financial and legal outputs require explicit human approval and appropriate organization and jurisdiction policies.

## Shared Agent Foundation

All domain agents will share:

- Versioned agent definitions and typed input/output schemas
- Permissioned tool registry
- Source-grounded retrieval and citations
- Source freshness and provenance
- Workflow state and resumable runs
- Human approval checkpoints
- Tenant-specific knowledge bases and policies
- Prompt-injection and data-exfiltration defenses
- Complete run, tool, document, and approval audit trails
- Cost, token, and latency accounting
- Domain evaluation suites and regression tests

## Grandice Privacy Shield

One common privacy engine will power three deployment modes:

1. Client-side Python and TypeScript SDKs
2. A local OpenAI-compatible privacy sidecar
3. A hosted redaction API

The client SDK and local sidecar are the zero-transfer options: original sensitive values are transformed inside the customer's environment before a request reaches Grandice.

The hosted API is a zero-retention option, not a zero-transfer option, because the original payload must reach the hosted service to be processed. Product documentation must state this distinction clearly.

Planned privacy APIs:

```text
POST /v1/privacy/detect
POST /v1/privacy/redact
POST /v1/privacy/tokenize
GET  /v1/privacy/policies/{id}
```

The shared privacy core will provide:

- Sensitive-entity detectors and validators
- Versioned policy definitions
- Irreversible masking
- Locally reversible tokenization
- Recursive structured-data processing
- Privacy receipts containing metadata only
- Accuracy, security, and regression evaluations

Token mappings used for rehydration must remain inside the customer's environment. Grandice must not receive or store those mappings.

## Multimodal Privacy

Privacy inspection and redaction must cover text, structured data, documents, images, audio, and video.

### Images and documents

- OCR text, including handwriting where supported
- Faces and other identifying visual characteristics
- Signatures
- Identity cards and financial documents
- License plates
- Barcodes and QR codes
- Screen captures and visible device identifiers
- EXIF, GPS, author, device, and other embedded metadata
- Hidden PDF layers, annotations, attachments, and document properties

### Audio

- Speech transcription followed by sensitive-entity detection
- Names, identifiers, account information, addresses, and confidential statements
- Speaker names and labels
- Voice biometric risk and configurable voice transformation
- Embedded tags and file metadata

### Video

- Frame-level OCR and visual detection
- Temporal tracking so redaction persists across frames
- Faces, license plates, screens, documents, badges, and QR codes
- Audio-track privacy processing
- Subtitles, captions, chapters, and metadata
- Verification that redaction covers the full duration, not sampled frames only

Multimodal redaction must process every relevant channel. Removing visible text while retaining it in audio, subtitles, metadata, thumbnails, or hidden document layers is considered a privacy failure.

## Privacy Enforcement and Receipts

Financial and legal APIs will support strict privacy policies and may reject requests containing detectable unredacted high-risk data.

Privacy receipts may include:

- Request identifier
- Processing location: client, sidecar, or hosted
- Policy and detector versions
- Sensitive-entity categories and counts
- Transformations applied
- Coverage and confidence warnings
- Whether raw content was transferred
- Whether content was retained

Receipts must never include original values, token maps, payload content, or reusable hashes of predictable personal data.

## Transparency Commitments

Grandice will publish:

- Data-flow diagrams for every deployment mode
- A retention matrix
- The exact metadata stored
- Supported sensitive-data categories and media types
- Redaction accuracy, confidence, and known limitations
- Subprocessor disclosures
- Security architecture and threat model
- Open-source client-side privacy components where practical

Claims such as "zero transfer" or "no personal data reaches Grandice" may only be used for requests protected locally before transmission.

## Delivery Direction

The intended sequence is:

1. Shared privacy core and policy model
2. Hosted privacy API and stable schemas
3. Python and TypeScript privacy SDKs
4. Local privacy sidecar
5. Domain API privacy enforcement
6. Shared agent runtime, audit model, retrieval, and evaluations
7. Production-quality domain verticals

