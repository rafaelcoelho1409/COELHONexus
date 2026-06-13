# Skill: rotator etiquette

You are running inside a free-tier LLM rotator (LiteLLM Router over ~20
NIM / Mistral / Groq / Gemini / Cerebras deployments). Several behaviors
help the rotator's cascade absorb provider quirks gracefully.

## Always emit tool_calls when the prompt asks

When the orchestrator system prompt says "call X", **emit a tool_call**.
Do not return prose like "I'll call X now" — the agent loop interprets
prose as your final answer and ends the conversation, leaving the actual
tool unset.

**Bad** (kills the scan):
```
I'll call discover_arxiv with query='deep agents' and n_max=30.
```

**Good** (continues the scan):
```
[tool_call: discover_arxiv(scan_id='abc-123', query='deep agents', n_max=30)]
```

## Parallel tool_calls in one message

When the prompt says "call A and B in parallel," emit **two tool_calls in
one assistant message**. Each provider that supports parallel function
calling will dispatch them concurrently; sequential providers will fall
back to one-at-a-time.

## Don't try to format JSON strings inside tool args

If a tool needs a long structured value (e.g. a list of papers), the tool
accepts it as a **structured argument**, not a string. Pass:

```
papers=[{"arxiv_id":"...", "title":"...", ...}, ...]
```

NOT:

```
papers_json="[\\"arxiv_id\\":...]"
```

The JSON-string pattern triggers character-perfect transcription which
free-tier models routinely truncate around 4-5KB.

## When cascading absorbs an error, don't retry yourself

The Router's bandit cools down failed arms automatically. If a previous
tool_call returned an error string, **don't loop trying it again with the
same args** — emit a different tool_call or end the phase. The Router
already picked a different deployment for your next call; your job is to
make progress, not retry.

## Thinking-block content

If you're a reasoning model that emits `<thinking>` blocks, that's fine
— our rotator wrapper strips them from message history before sending to
the next provider. You don't need to inhibit them.
