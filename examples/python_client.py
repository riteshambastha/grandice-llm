"""Connecting a Python application to the Grandice LLM gateway.

The gateway speaks the OpenAI wire protocol, so the official `openai` package
works unmodified - only base_url and api_key change.

    pip install openai
    set GRANDICE_API_KEY=gll-...
    python examples/python_client.py
"""

import base64
import json
import os

from openai import OpenAI

client = OpenAI(
    base_url=os.environ.get("GRANDICE_BASE_URL", "http://127.0.0.1:8080/v1"),
    api_key=os.environ["GRANDICE_API_KEY"],
)


def simple_chat() -> None:
    resp = client.chat.completions.create(
        model="chat",  # alias; resolves to whatever config/models.json points at
        messages=[
            {"role": "system", "content": "You are concise."},
            {"role": "user", "content": "Name three uses for a local LLM."},
        ],
    )
    print(resp.choices[0].message.content)
    print(f"[tokens: {resp.usage.total_tokens}]")


def streaming_chat() -> None:
    stream = client.chat.completions.create(
        model="chat",
        messages=[{"role": "user", "content": "Count from 1 to 10."}],
        stream=True,
    )
    for chunk in stream:
        # The final chunk carries usage and has no choices, so guard the index.
        if chunk.choices and chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)
    print()


def deliberate_reasoning() -> None:
    """Thinking is off by default. Ask for it when a task genuinely needs it."""
    resp = client.chat.completions.create(
        model="chat",
        messages=[{"role": "user", "content": "A bat and ball cost $1.10. The bat costs $1 more than the ball. What does the ball cost?"}],
        reasoning_effort="medium",
        max_tokens=2000,
    )
    print(resp.choices[0].message.content)


def write_code() -> None:
    resp = client.chat.completions.create(
        model="code",
        messages=[{"role": "user", "content": "Write a Python function that reverses words in a sentence."}],
    )
    print(resp.choices[0].message.content)


def embed_documents() -> None:
    resp = client.embeddings.create(
        model="embed",
        input=["The cat sat on the mat.", "Felines rest on rugs.", "Diesel engines are loud."],
    )
    vectors = [d.embedding for d in resp.data]
    print(f"{len(vectors)} vectors of dimension {len(vectors[0])}")

    def cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb)

    print(f"related sentences   : {cosine(vectors[0], vectors[1]):.3f}")
    print(f"unrelated sentences : {cosine(vectors[0], vectors[2]):.3f}")


def describe_image(path: str) -> None:
    with open(path, "rb") as fh:
        encoded = base64.b64encode(fh.read()).decode()

    resp = client.chat.completions.create(
        model="vision",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image in one sentence."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                ],
            }
        ],
    )
    print(resp.choices[0].message.content)


def call_tools() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Current weather for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]

    resp = client.chat.completions.create(
        model="chat",
        messages=[{"role": "user", "content": "What's the weather in Mumbai?"}],
        tools=tools,
    )
    calls = resp.choices[0].message.tool_calls
    if not calls:
        print("Model answered directly:", resp.choices[0].message.content)
        return
    for call in calls:
        print(f"{call.function.name}({json.loads(call.function.arguments)})")


if __name__ == "__main__":
    for label, fn in [
        ("chat", simple_chat),
        ("streaming", streaming_chat),
        ("reasoning", deliberate_reasoning),
        ("code", write_code),
        ("embeddings", embed_documents),
        ("tools", call_tools),
    ]:
        print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
        fn()
