import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { PrivacyEngine, createPrivacyFetch, detectText } from "./index.js";

test("passes the language-neutral conformance cases", () => {
  const fixture = JSON.parse(
    readFileSync(
      new URL("../../../config/privacy-conformance.json", import.meta.url),
      "utf8",
    ),
  ) as {
    cases: Array<{
      id: string;
      policy: string;
      input: string;
      entities: string[];
    }>;
  };
  for (const item of fixture.cases) {
    assert.deepEqual(
      detectText(item.input, item.policy).map((finding) => finding.entityType),
      item.entities,
      item.id,
    );
  }
});

test("detects sensitive values without returning their contents", () => {
  const findings = detectText(
    "Email alice@example.com or use SSN 123-45-6789.",
    "strict-v1",
  );
  assert.deepEqual(
    findings.map((finding) => finding.entityType),
    ["EMAIL_ADDRESS", "US_SSN"],
  );
  assert.equal("value" in findings[0]!, false);
});

test("tokenizes recursively and restores only in the local vault", () => {
  const engine = new PrivacyEngine("financial-strict-v1");
  const original = {
    messages: [
      {
        role: "user",
        content: "Email alice@example.com about account 87432291.",
      },
    ],
  };
  const protectedResult = engine.tokenize(original);
  assert.notDeepEqual(protectedResult.data, original);
  assert.match(protectedResult.data.messages[0]!.content, /<GI_EMAIL_ADDRESS_001>/);
  assert.deepEqual(engine.restore(protectedResult.data), original);
  assert.equal(protectedResult.receipt.rawContentTransferred, false);
});

test("refuses embedded media when local coverage is incomplete", () => {
  const engine = new PrivacyEngine("strict-v1");
  assert.throws(
    () =>
      engine.tokenize({
        image_url: {
          url: `data:image/png;base64,${"A".repeat(300)}`,
        },
      }),
    /was not transferred/,
  );
});

test("refuses remote media references", () => {
  const engine = new PrivacyEngine("strict-v1");
  assert.throws(
    () =>
      engine.tokenize({
        image_url: { url: "https://files.example/private-passport.png" },
      }),
    /remote media/,
  );
});

test("safe response restoration excludes tool arguments", () => {
  const engine = new PrivacyEngine("strict-v1");
  const protectedResult = engine.tokenize({
    content: "Email alice@example.com",
  });
  const token = protectedResult.data.content.split(" ").at(-1)!;
  const restored = engine.restoreResponse({
    content: `Visible ${token}`,
    tool_calls: [{ function: { arguments: JSON.stringify({ email: token }) } }],
  });

  assert.equal(restored.content, "Visible alice@example.com");
  assert.match(restored.tool_calls[0]!.function.arguments, /GI_EMAIL_ADDRESS/);
});

test("fetch wrapper protects Request objects and rejects binary bodies", async () => {
  let transmitted = "";
  const mockFetch: typeof fetch = async (_input, init) => {
    transmitted = String(init?.body ?? "");
    return new Response(JSON.stringify({ content: "ok" }), {
      headers: { "content-type": "application/json" },
    });
  };
  const privacyFetch = createPrivacyFetch({ fetchImplementation: mockFetch });
  await privacyFetch(
    new Request("https://llm.example/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: "Email alice@example.com" }),
    }),
  );
  assert.doesNotMatch(transmitted, /alice@example\.com/);
  assert.match(transmitted, /GI_EMAIL_ADDRESS/);

  await assert.rejects(
    privacyFetch("https://llm.example/v1/chat/completions", {
      method: "POST",
      body: new Blob(["alice@example.com"]),
    }),
    /unsupported body was not sent/,
  );
});

