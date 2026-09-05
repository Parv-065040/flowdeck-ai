# Flowdeck AI

A live, voice-based AI sales & negotiation agent. A prospective customer calls in and talks to
**Rae**, an AI sales rep, over a real-time voice call. Rae checks calendar availability, books
real meetings, answers product questions from a knowledge base, logs requirements and
objections, and escalates to a human when needed — all through natural voice conversation, not
a scripted IVR tree.

Built by **Team Spartans** for [hackathon name].

**Demo video:** [link here]

---

## What it does

- Full real-time voice conversation with an AI sales agent (not a chatbot with a voice bolted on)
- Real Google Calendar integration — Rae checks live availability and books actual meetings
- RAG-backed product knowledge base (Qdrant) so Rae can answer questions accurately
- Objection and requirement logging during the call
- Escalation to a human rep when the conversation needs it
- A live "deal sheet" in the frontend that fills itself in as the call progresses — contact name,
  use case, budget signal, competitor mentions, next action

## Tech stack

| Layer | Technology |
|---|---|
| Voice infrastructure | Agora Conversational AI Engine (STT via Deepgram, TTS via MiniMax, RTC) |
| Backend | Python FastAPI — OpenAI-compatible `/v1/chat/completions` endpoint (Agora's Bring-Your-Own-LLM contract) |
| Reasoning | Groq (primary) → Gemini (fallback) → local Ollama (optional) → rule-based (last resort) |
| Frontend | React + Tailwind |
| Data | Postgres (calls, tool logs, deal sheets, objections), Qdrant (RAG vector store) |
| Calendar | Google Calendar API (real booking; falls back to mock slots if unconfigured) |
| Local dev tunnel | Cloudflare Tunnel (exposes `localhost:8000` for Agora's cloud to reach) |

See [`docs/TECHNICAL_ARCHITECTURE.md`](docs/TECHNICAL_ARCHITECTURE.md) for the full architecture
writeup and [`docs/AGORA_INTEGRATION.md`](docs/AGORA_INTEGRATION.md) for details on how Agora's
technologies are integrated.

## Repo structure

```
flowdeck-ai/
├── backend/           # FastAPI service — reasoning loop, tools, Agora session management
├── frontend/          # React + Tailwind product UI
├── demo/              # Bare-bones HTML test harness (fallback/reference, not the main UI)
├── docs/              # Architecture, Agora integration, and setup docs
├── docker-compose.yml # Orchestrates backend + postgres + qdrant
└── .env.example       # Template for all required secrets/config
```

## Quickstart

Requires: Docker, Node.js, and a `cloudflared` install.

```bash
# 1. Copy env template and fill in your keys (see "Environment" below)
cp .env.example .env

# 2. Start backend + postgres + qdrant
docker compose up --build -d

# 3. In a separate terminal, open a public tunnel to the backend (stays open)
cloudflared tunnel --url http://localhost:8000

# 4. In a separate terminal, run the frontend
cd frontend
npm install
npm run dev
```

Paste the `cloudflared` URL into the frontend's **"Public backend URL"** field before starting a
call. Use a fresh channel name per test call to avoid stale-state issues.

## Environment

Required in `.env` (see `.env.example` for the full template):

- `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE` — from [console.agora.io](https://console.agora.io),
  project must have Conversational AI / Voice Agent Builder enabled, and RTM enabled
  (`agora project feature enable rtm` via the Agora CLI)
- `GROQ_API_KEY`, `GEMINI_API_KEY` — reasoning tiers
- `GOOGLE_CALENDAR_CREDENTIALS_JSON`, `GOOGLE_CALENDAR_ID` — optional; falls back to mock slots
  if absent. Needs a Google Cloud service account with Calendar API enabled, and the target
  calendar shared with that service account's email.

## Known limitations (accepted for hackathon submission)

- Live transcript panel was removed before submission — it was built but failed to initialize;
  the voice call itself never depended on it. See `docs/TECHNICAL_ARCHITECTURE.md` for retry notes.
- No token-level streaming from the LLM to Agora — the backend runs its full reasoning loop, then
  sends one complete response.
- Cloudflare's free quick tunnels generate a new URL on every restart — fine for local dev/demo,
  not a permanent deployment.
- No authentication on backend endpoints — fine for a hackathon demo.

## Team

Team Spartans — [names / roles here]
