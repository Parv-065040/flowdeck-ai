# Flowdeck AI — Backend

Python FastAPI service that Agora's Conversational AI Engine calls as a "Bring Your Own LLM"
endpoint on every conversational turn.

## Structure

```
backend/
├── main.py                   # FastAPI entrypoint — /healthz, /calls/*, /v1/chat/completions
├── config.py                 # Pydantic settings, reads .env
├── agora_integration.py      # /api/token, /api/invite-agent, /api/stop-conversation, /api/config
│                              # — builds the Agora Agent (STT/TTS/LLM config), owns call_id continuity
├── db/
│   ├── connection.py         # asyncpg connection pool
│   └── schema.sql            # calls, tool_calls, deal_sheets, objections tables
├── llm/
│   ├── agent_loop.py         # Tool-calling reasoning loop (run_agent_turn)
│   ├── reasoning_chain.py    # Groq → Gemini → Ollama → rule-based fallback chain
│   └── rule_based_fallback.py
├── tools/
│   ├── definitions.py        # SYSTEM_PROMPT + tool schemas
│   ├── executor.py           # Executes a tool call, writes to Postgres
│   ├── google_calendar.py    # Real Google Calendar API integration
│   ├── rag.py                # Qdrant-backed knowledge base lookup
│   └── seed_knowledge_base.py
└── scripts/
    └── test_reasoning_locally.py   # Standalone reasoning-chain test, no voice stack required
```

## Endpoints

| Route | Purpose |
|---|---|
| `POST /v1/chat/completions` | The endpoint Agora's Conversational AI Engine calls each turn. OpenAI-compatible request/response shape. |
| `GET /healthz` | Live status check for Postgres, Qdrant, Groq, Gemini. |
| `POST /api/token` | Issues an Agora RTC token for the frontend to join a channel. |
| `POST /api/invite-agent` | Starts an Agora Conversational AI agent session for a channel. |
| `POST /api/stop-conversation` | Cleanly ends an agent session. |
| `GET /api/config` | Returns current Agora/session config to the frontend. |

## Running locally (without Docker)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Requires Postgres and Qdrant running separately, or use `docker compose up postgres qdrant` from
the repo root and point `.env` at them.

## Testing the reasoning chain without a live call

```bash
python scripts/test_reasoning_locally.py
```

Exercises `agent_loop.py` / `reasoning_chain.py` directly — useful for iterating on tool-calling
behavior without spinning up Agora, Cloudflare, or the frontend.

## Known bug classes to watch for (see root docs for full debugging history)

- **Trailing whitespace in any URL fed to Agora's Start Agent API silently breaks the LLM
  endpoint** — no error surfaces, Agora just never calls the backend. `.strip()` is applied in
  `agora_integration.py`, but re-check if this class of bug resurfaces.
- **Calendar slot strings must round-trip through the exact RFC3339 format `get_available_slots()`
  produces** before being sent to `book_slot()` — freeform display strings will fail every booking.
- **The `calls` row must exist before the tool-calling loop runs**, not just before logging
  afterward, or a `book_meeting` tool call can hit a missing foreign key and crash the turn.
