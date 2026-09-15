export type Transformation = "detect" | "mask" | "tokenize";
export type ProcessingLocation = "client" | "sidecar" | "hosted";

export interface Finding {
  entityType: string;
  path: string;
  start: number;
  end: number;
  confidence: number;
  detector: string;
  replacement?: string;
}

export interface PrivacyReceipt {
  requestId: string;
  processingLocation: ProcessingLocation;
  policy: string;
  transformation: Transformation;
  entitiesDetected: Record<string, number>;
  entitiesTransformed: Record<string, number>;
  rawContentTransferred: boolean;
  contentRetained: false;
  warnings: string[];
}

export interface PrivacyResult<T = unknown> {
  data: T;
  findings: Finding[];
  receipt: PrivacyReceipt;
}

interface Detector {
  entityType: string;
  source: string;
  flags: string;
  confidence: number;
  validate?: (value: string) => boolean;
}

const GENERAL_ENTITIES = new Set([
  "EMAIL_ADDRESS",
  "PHONE_NUMBER",
  "IP_ADDRESS",
  "US_SSN",
  "CREDIT_CARD",
  "IBAN",
  "API_KEY",
]);

const POLICY_ENTITIES: Record<string, Set<string>> = {
  "general-v1": GENERAL_ENTITIES,
  "strict-v1": new Set([...GENERAL_ENTITIES, "ACCOUNT_NUMBER", "DATE_OF_BIRTH"]),
  "financial-strict-v1": new Set([
    ...GENERAL_ENTITIES,
    "ACCOUNT_NUMBER",
    "ROUTING_NUMBER",
    "DATE_OF_BIRTH",
    "FINANCIAL_VALUE",
  ]),
  "legal-strict-v1": new Set([
    ...GENERAL_ENTITIES,
    "ACCOUNT_NUMBER",
    "DATE_OF_BIRTH",
    "CASE_NUMBER",
  ]),
};

function luhn(value: string): boolean {
  const digits = [...value].filter((char) => /\d/.test(char)).map(Number);
  if (digits.length < 13 || digits.length > 19) return false;
  let checksum = 0;
  const parity = digits.length % 2;
  digits.forEach((rawDigit, index) => {
    let digit = rawDigit;
    if (index % 2 === parity) {
      digit *= 2;
      if (digit > 9) digit -= 9;
    }
    checksum += digit;
  });
  return checksum % 10 === 0;
}

function validIban(value: string): boolean {
  const compact = value.replace(/\s/g, "").toUpperCase();
  if (
    compact.length < 15 ||
    compact.length > 34 ||
    !/^[A-Z]{2}\d{2}[A-Z0-9]+$/.test(compact)
  ) {
    return false;
  }
  const rearranged = compact.slice(4) + compact.slice(0, 4);
  let remainder = 0;
  for (const character of rearranged) {
    const expanded = /[A-Z]/.test(character)
      ? String(character.charCodeAt(0) - 55)
      : character;
    for (const digit of expanded) remainder = (remainder * 10 + Number(digit)) % 97;
  }
  return remainder === 1;
}

const DETECTORS: Detector[] = [
  {
    entityType: "EMAIL_ADDRESS",
    source: String.raw`(?<![\w.+-])[\w.+-]+@(?:[\w-]+\.)+[A-Za-z]{2,63}(?![\w-])`,
    flags: "g",
    confidence: 0.98,
  },
  {
    entityType: "US_SSN",
    source: String.raw`(?<!\d)(?!000|666|9\d\d)\d{3}[- ](?!00)\d{2}[- ](?!0000)\d{4}(?!\d)`,
    flags: "g",
    confidence: 0.98,
  },
  {
    entityType: "CREDIT_CARD",
    source: String.raw`(?<!\d)(?:\d[ -]*?){13,19}(?!\d)`,
    flags: "g",
    confidence: 0.95,
    validate: luhn,
  },
  {
    entityType: "IBAN",
    source: String.raw`(?<![A-Z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}(?![A-Z0-9])`,
    flags: "gi",
    confidence: 0.96,
    validate: validIban,
  },
  {
    entityType: "IP_ADDRESS",
    source: String.raw`(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])`,
    flags: "g",
    confidence: 0.9,
  },
  {
    entityType: "PHONE_NUMBER",
    source: String.raw`(?<!\w)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]\d{4}(?!\w)`,
    flags: "g",
    confidence: 0.82,
  },
  {
    entityType: "DATE_OF_BIRTH",
    source: String.raw`\b(?:dob|date\s+of\s+birth|born)\s*(?::|is)?\s*(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},?\s+\d{4})`,
    flags: "gi",
    confidence: 0.92,
  },
  {
    entityType: "ROUTING_NUMBER",
    source: String.raw`\b(?:routing|aba)\s*(?:number|no\.?|#)?\s*[:=-]?\s*\d{9}\b`,
    flags: "gi",
    confidence: 0.94,
  },
  {
    entityType: "ACCOUNT_NUMBER",
    source: String.raw`\b(?:account|acct)\s*(?:number|no\.?|#)?\s*[:=-]?\s*[A-Z0-9-]{6,24}\b`,
    flags: "gi",
    confidence: 0.9,
  },
  {
    entityType: "CASE_NUMBER",
    source: String.raw`\b(?:case|matter|docket)\s*(?:number|no\.?|#)?\s*[:=-]?\s*[A-Z0-9-]{4,30}\b`,
    flags: "gi",
    confidence: 0.88,
  },
  {
    entityType: "FINANCIAL_VALUE",
    source: String.raw`(?<!\w)(?:USD|CAD|GBP|EUR|\$|£|€)\s?\d[\d,]*(?:\.\d{1,2})?(?!\w)`,
    flags: "gi",
    confidence: 0.86,
  },
  {
    entityType: "API_KEY",
    source: String.raw`\b(?:api[_ -]?key|secret|token|password)\s*[:=]\s*['"]?[A-Za-z0-9_./+=-]{12,}['"]?`,
    flags: "gi",
    confidence: 0.93,
  },
];

function overlaps(a: Finding, b: Finding): boolean {
  return a.start < b.end && b.start < a.end;
}

export function detectText(text: string, policy = "general-v1", path = "$"): Finding[] {
  const entities = POLICY_ENTITIES[policy];
  if (!entities) {
    throw new Error(`Unknown privacy policy '${policy}'.`);
  }
  const candidates: Finding[] = [];
  for (const detector of DETECTORS) {
    if (!entities.has(detector.entityType)) continue;
    const expression = new RegExp(detector.source, detector.flags);
    for (const match of text.matchAll(expression)) {
      const value = match[0];
      if (detector.validate && !detector.validate(value)) continue;
      const start = match.index;
      candidates.push({
        entityType: detector.entityType,
        path,
        start,
        end: start + value.length,
        confidence: detector.confidence,
        detector: `pattern:${detector.entityType.toLowerCase()}`,
      });
    }
  }
  candidates.sort(
    (a, b) =>
      b.confidence - a.confidence ||
      b.end - b.start - (a.end - a.start) ||
      a.start - b.start,
  );
  const accepted: Finding[] = [];
  for (const finding of candidates) {
    if (!accepted.some((item) => overlaps(item, finding))) accepted.push(finding);
  }
  return accepted.sort((a, b) => a.start - b.start);
}

export class TokenVault {
  private readonly tokenToValue = new Map<string, string>();
  private readonly valueToToken = new Map<string, string>();
  private readonly counts = new Map<string, number>();

  tokenFor(entityType: string, value: string): string {
    const key = `${entityType}\0${value}`;
    const existing = this.valueToToken.get(key);
    if (existing) return existing;
    const count = (this.counts.get(entityType) ?? 0) + 1;
    this.counts.set(entityType, count);
    const token = `<GI_${entityType}_${count.toString().padStart(3, "0")}>`;
    this.valueToToken.set(key, token);
    this.tokenToValue.set(token, value);
    return token;
  }

  restore<T>(data: T): T {
    return walkStrings(data, (text) => {
      let output = text;
      for (const [token, value] of this.tokenToValue) {
        output = output.replaceAll(token, value);
      }
      return output;
    });
  }

  restoreResponse<T>(data: T): T {
    const safeTextKeys = new Set(["content", "text", "output_text"]);
    const blockedKeys = new Set([
      "tool_calls",
      "tool_call",
      "function_call",
      "function",
      "arguments",
      "input",
    ]);
    const walk = (value: unknown, parentKey?: string): unknown => {
      if (typeof value === "string") {
        if (!parentKey || !safeTextKeys.has(parentKey)) return value;
        let output = value;
        for (const [token, original] of this.tokenToValue) {
          output = output.replaceAll(token, original);
        }
        return output;
      }
      if (Array.isArray(value)) return value.map((item) => walk(item, parentKey));
      if (value !== null && typeof value === "object") {
        return Object.fromEntries(
          Object.entries(value).map(([key, item]) => [
            key,
            blockedKeys.has(key.toLowerCase()) ? item : walk(item, key.toLowerCase()),
          ]),
        );
      }
      return value;
    };
    return walk(data) as T;
  }
}

function walkStrings<T>(data: T, transform: (text: string, path: string) => string, path = "$"): T {
  if (typeof data === "string") return transform(data, path) as T;
  if (Array.isArray(data)) {
    return data.map((item, index) => walkStrings(item, transform, `${path}[${index}]`)) as T;
  }
  if (data !== null && typeof data === "object") {
    return Object.fromEntries(
      Object.entries(data).map(([key, value]) => [
        key,
        walkStrings(value, transform, `${path}.${key}`),
      ]),
    ) as T;
  }
  return data;
}

function masked(entityType: string, value: string): string {
  if (entityType === "EMAIL_ADDRESS" && value.includes("@")) {
    const [local = "", domain = ""] = value.split("@", 2);
    return `${local.slice(0, 1)}***@${domain}`;
  }
  return `<REDACTED_${entityType}>`;
}

function embeddedMediaType(value: string, path: string): string | undefined {
  const dataUrl = /^data:(image|audio|video)\/[^;,]+(?:;[^,]+)*;base64,/i.exec(value);
  if (dataUrl?.[1]) return dataUrl[1].toLowerCase();
  const mediaPath = /(image|audio|video|attachment)/i.test(path);
  if (mediaPath && /^https?:\/\//i.test(value)) return "remote";
  const compact = value.replace(/\s/g, "");
  if (mediaPath && compact.length >= 256 && /^[A-Za-z0-9+/]+={0,2}$/.test(compact)) {
    return "embedded";
  }
  return undefined;
}

export class PrivacyEngine {
  readonly vault: TokenVault;

  constructor(
    readonly policy = "general-v1",
    readonly processingLocation: ProcessingLocation = "client",
    vault?: TokenVault,
  ) {
    if (!POLICY_ENTITIES[policy]) throw new Error(`Unknown privacy policy '${policy}'.`);
    this.vault = vault ?? new TokenVault();
  }

  inspect<T>(data: T): PrivacyResult<T> {
    return this.process(data, "detect");
  }

  mask<T>(data: T): PrivacyResult<T> {
    return this.process(data, "mask");
  }

  tokenize<T>(data: T): PrivacyResult<T> {
    return this.process(data, "tokenize");
  }

  restore<T>(data: T): T {
    return this.vault.restore(data);
  }

  restoreResponse<T>(data: T): T {
    return this.vault.restoreResponse(data);
  }

  private process<T>(data: T, transformation: Transformation): PrivacyResult<T> {
    const findings: Finding[] = [];
    const output = walkStrings(data, (text, path) => {
      const mediaType = embeddedMediaType(text, path);
      if (mediaType && transformation !== "detect") {
        throw new Error(
          `Embedded ${mediaType} media at ${path} was not transferred. ` +
            "Complete local multimodal redaction is not available in this build.",
        );
      }
      const detected = detectText(text, this.policy, path);
      findings.push(...detected);
      if (transformation === "detect") return text;
      let transformed = text;
      for (const finding of [...detected].reverse()) {
        const original = text.slice(finding.start, finding.end);
        const replacement =
          transformation === "mask"
            ? masked(finding.entityType, original)
            : this.vault.tokenFor(finding.entityType, original);
        finding.replacement = replacement;
        transformed =
          transformed.slice(0, finding.start) + replacement + transformed.slice(finding.end);
      }
      return transformed;
    });
    const counts: Record<string, number> = {};
    for (const finding of findings) {
      counts[finding.entityType] = (counts[finding.entityType] ?? 0) + 1;
    }
    return {
      data: output,
      findings,
      receipt: {
        requestId: `prv_${globalThis.crypto.randomUUID()}`,
        processingLocation: this.processingLocation,
        policy: this.policy,
        transformation,
        entitiesDetected: counts,
        entitiesTransformed: transformation === "detect" ? {} : { ...counts },
        rawContentTransferred: this.processingLocation === "hosted",
        contentRetained: false,
        warnings: [],
      },
    };
  }
}

export interface PrivacyFetchOptions {
  policy?: string;
  fetchImplementation?: typeof fetch;
}

export function createPrivacyFetch(options: PrivacyFetchOptions = {}): typeof fetch {
  const policy = options.policy ?? "strict-v1";
  const fetchImplementation = options.fetchImplementation ?? globalThis.fetch;

  return async (input: URL | RequestInfo, init?: RequestInit): Promise<Response> => {
    const sourceRequest = input instanceof Request ? input : undefined;
    const method = (init?.method ?? sourceRequest?.method ?? "GET").toUpperCase();
    let body: BodyInit | null | undefined = init?.body;
    if (body === undefined && sourceRequest && sourceRequest.body !== null) {
      if (sourceRequest.bodyUsed) {
        throw new Error("A consumed Request body cannot be privacy inspected.");
      }
      body = await sourceRequest.clone().text();
    }
    if (body === undefined || body === null || body === "") {
      return fetchImplementation(input, init);
    }
    if (typeof body !== "string") {
      throw new Error(
        "Zero-transfer mode accepts JSON string bodies only; unsupported body was not sent.",
      );
    }
    if (method === "GET" || method === "HEAD") {
      throw new Error(`${method} requests cannot carry a protected body.`);
    }
    let requestData: unknown;
    try {
      requestData = JSON.parse(body);
    } catch {
      throw new Error("Zero-transfer mode requires a valid JSON request body.");
    }
    if (
      requestData !== null &&
      typeof requestData === "object" &&
      "stream" in requestData &&
      (requestData as { stream?: boolean }).stream
    ) {
      throw new Error("Streaming rehydration is not yet supported safely.");
    }
    // A request-scoped vault prevents values from accumulating or crossing
    // tenant/session boundaries in a long-lived browser or Node process.
    const engine = new PrivacyEngine(policy, "client");
    const protectedData = engine.tokenize(requestData).data;
    const headers = new Headers(sourceRequest?.headers);
    new Headers(init?.headers).forEach((value, name) => headers.set(name, value));
    headers.set("content-type", "application/json");
    headers.set("X-Grandice-Privacy-Mode", "client");
    headers.set("X-Grandice-Privacy-Policy", engine.policy);
    const response = await fetchImplementation(input, {
      ...init,
      headers,
      body: JSON.stringify(protectedData),
    });
    const responseType = response.headers.get("content-type") ?? "";
    if (!responseType.startsWith("application/json")) return response;
    const locallySafe = engine.mask(await response.json()).data;
    const restored = engine.restoreResponse(locallySafe);
    const responseHeaders = new Headers(response.headers);
    for (const header of [
      "content-length",
      "content-encoding",
      "transfer-encoding",
      "etag",
      "content-md5",
    ]) {
      responseHeaders.delete(header);
    }
    return new Response(JSON.stringify(restored), {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  };
}

