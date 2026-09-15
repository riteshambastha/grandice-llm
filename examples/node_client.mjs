/**
 * Connecting a Node.js application to the Grandice LLM gateway.
 *
 *   npm install openai
 *   $env:GRANDICE_API_KEY="gll-..."
 *   node examples/node_client.mjs
 */

import OpenAI from "openai";

const client = new OpenAI({
  baseURL: process.env.GRANDICE_BASE_URL ?? "http://127.0.0.1:8080/v1",
  apiKey: process.env.GRANDICE_API_KEY,
});

async function simpleChat() {
  const resp = await client.chat.completions.create({
    model: "chat",
    messages: [{ role: "user", content: "Name three uses for a local LLM." }],
  });
  console.log(resp.choices[0].message.content);
}

async function streamingChat() {
  const stream = await client.chat.completions.create({
    model: "chat",
    messages: [{ role: "user", content: "Count from 1 to 10." }],
    stream: true,
  });
  for await (const chunk of stream) {
    // The usage-bearing final chunk has an empty choices array.
    const delta = chunk.choices?.[0]?.delta?.content;
    if (delta) process.stdout.write(delta);
  }
  console.log();
}

async function structuredOutput() {
  const resp = await client.chat.completions.create({
    model: "chat",
    messages: [{ role: "user", content: "Extract name and age: 'Ravi is 34 years old.'" }],
    response_format: {
      type: "json_schema",
      json_schema: {
        name: "person",
        schema: {
          type: "object",
          properties: { name: { type: "string" }, age: { type: "integer" } },
          required: ["name", "age"],
        },
      },
    },
  });
  console.log(JSON.parse(resp.choices[0].message.content));
}

async function embed() {
  const resp = await client.embeddings.create({
    model: "embed",
    input: ["local models", "self-hosted inference"],
  });
  console.log(`${resp.data.length} vectors, dimension ${resp.data[0].embedding.length}`);
}

for (const [label, fn] of [
  ["chat", simpleChat],
  ["streaming", streamingChat],
  ["structured output", structuredOutput],
  ["embeddings", embed],
]) {
  console.log(`\n${"=".repeat(60)}\n${label}\n${"=".repeat(60)}`);
  await fn();
}
