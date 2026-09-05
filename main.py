"""
Flowdeck AI backend.

Two jobs:
1. Expose an OpenAI-API-compatible /v1/chat/completions endpoint that Agora's Conversational AI
   Engine calls as a "custom LLM" (see Agora docs: Connect your own LLM service). This is the
   integration point built on Day 2 of the production plan.
2. Manage call lifecycle (start/end) and execute tool calls against real Postgres/Calendar/Qdrant,
   replacing the prototype's browser-memory mock data.

Day 1 goal: everything below /v1/chat/completions and /healthz should work and be testable via
scripts/test_reasoning_locally.py WITHOUT Agora in the loop at all. Don't touch Agora until this
is solid — see Section 8 of PRODUCTION_PLAN.md.
"""
import json
import logging
import time
import uuid

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db.connection import get_pool, close_pool
from config import settings
from llm.agent_loop import run_agent_turn
from tools.definitions import SYSTEM_PROMPT, TOOLS
from agora_integration import router as agora_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("flowdeck.main")

app = FastAPI(title="Flowdeck AI Backend")

# CORS: needed once the frontend (running on a different origin/port) starts calling this
# backend directly — e.g. the Day 2 test client and the Day 3 React app. Wide open for
# hackathon development; tighten to specific origins before any real production use.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agora_router)


# ---------- health ----------
@app.get("/healthz")
async def healthz():
    """
    Checked by docker-compose's healthcheck AND should be checked manually before every dev
    session and before the live demo, per Section 6 of PRODUCTION_PLAN.md. Returns per-dependency
    status so a broken link is obvious immediately, not discovered mid-call.
    """
    checks = {}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"FAIL: {e}"

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.qdrant_url}/healthz")
            checks["qdrant"] = "ok" if resp.status_code == 200 else f"FAIL: status {resp.status_code}"
    except Exception as e:
        checks["qdrant"] = f"FAIL: {e}"

    try:
        if settings.groq_api_key:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    f"{settings.groq_base_url}/models",
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                )
                checks["groq"] = "ok" if resp.status_code == 200 else f"FAIL: status {resp.status_code}"
        else:
            checks["groq"] = "FAIL: GROQ_API_KEY not set"
    except Exception as e:
        checks["groq"] = f"FAIL: {e}"

    try:
        if settings.gemini_api_key:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    f"{settings.gemini_base_url}/models",
                    headers={"Authorization": f"Bearer {settings.gemini_api_key}"},
                )
                checks["gemini"] = "ok" if resp.status_code == 200 else f"FAIL: status {resp.status_code}"
        else:
            checks["gemini"] = "FAIL: GEMINI_API_KEY not set"
    except Exception as e:
        checks["gemini"] = f"FAIL: {e}"

    # Ollama is an optional 4th tier, off by default — don't fail overall health just because it's off
    if settings.ollama_enabled:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{settings.ollama_base_url}/api/tags")
                checks["ollama"] = "ok" if resp.status_code == 200 else f"FAIL: status {resp.status_code}"
        except Exception as e:
            checks["ollama"] = f"FAIL: {e}"
    else:
        checks["ollama"] = "disabled (optional 4th tier)"

    # Only Groq/Gemini/Postgres/Qdrant need to be healthy for an "ok" overall status —
    # Ollama being disabled is expected, not a failure.
    required = ["postgres", "qdrant", "groq", "gemini"]
    all_ok = all(checks[k] == "ok" for k in required)
    return {"status": "ok" if all_ok else "degraded", "checks": checks}


# ---------- call lifecycle ----------
class StartCallResponse(BaseModel):
    call_id: str


@app.post("/calls/start", response_model=StartCallResponse)
async def start_call(agora_channel: str | None = None):
    pool = await get_pool()
    call_id = str(uuid.uuid4())
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO calls (id, agora_channel) VALUES ($1, $2)", uuid.UUID(call_id), agora_channel
        )
    return StartCallResponse(call_id=call_id)


@app.post("/calls/{call_id}/end")
async def end_call(call_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE calls SET ended_at = now() WHERE id = $1", uuid.UUID(call_id))
    return {"ended": True}


@app.get("/calls/{call_id}/deal-sheet")
async def get_deal_sheet(call_id: str):
    """Used by the frontend to render the live deal sheet panel."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        deal_sheet = await conn.fetchrow("SELECT * FROM deal_sheets WHERE call_id = $1", uuid.UUID(call_id))
        objections = await conn.fetch(
            "SELECT objection_text, raised_at FROM objections WHERE call_id = $1 ORDER BY raised_at", uuid.UUID(call_id)
        )
        call = await conn.fetchrow("SELECT status, reasoning_mode FROM calls WHERE id = $1", uuid.UUID(call_id))
    if not call:
        raise HTTPException(404, "call not found")
    return {
        "deal_sheet": dict(deal_sheet) if deal_sheet else {},
        "objections": [dict(o) for o in objections],
        "status": call["status"],
        "reasoning_mode": call["reasoning_mode"],
    }


# ---------- the OpenAI-compatible endpoint Agora calls ----------
class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    tools: list[dict] | None = None
    stream: bool = False
    # Agora passes call/session context via metadata in real integrations — adjust this field
    # name to match whatever Agora's actual request shape turns out to be once you're on Day 2
    # and can inspect real traffic. This is a reasonable placeholder, not verified against a live
    # Agora session yet.
    call_id: str | None = None
    # Confirmed live during Day 2 testing: this stays None on every real Agora request — Agora
    # does not send a `call_id` field at all, at least not under this name. `context` is captured
    # here (undeclared previously, so pydantic was silently dropping it) on the chance Agora uses
    # this field for session/channel identity instead, per the field showing up in Agora's own
    # documented ChatCompletionRequest shape. Logged once per request below so the next live test
    # tells us definitively what's actually available, instead of guessing a second time.
    context: dict | None = None

    class Config:
        extra = "allow"  # don't silently drop any other fields Agora sends — surface them in logs


async def _ensure_call_row(call_uuid: uuid.UUID, agora_channel: str | None = None):
    """Guarantees a `calls` row exists for this ID before any tool writes (deal_sheets,
    objections, tool_calls) that FK-reference it. Confirmed live during Day 2 testing that
    without this running BEFORE the tool-calling loop, a real customer-facing action
    (book_meeting) crashed the entire turn with a ForeignKeyViolationError — not just a
    logging nicety, an actual demo-breaking bug."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO calls (id, agora_channel) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
                call_uuid, agora_channel,
            )
    except Exception as e:
        logger.warning(f"Failed to ensure calls row exists (non-fatal): {e}")


async def _log_turn(call_id: str, turn_result):
    """Shared logging helper — records every tool call and the final reasoning tier, used by
    the streaming endpoint below. Split out so the streaming generator stays readable."""
    pool = await get_pool()
    call_uuid = uuid.UUID(call_id) if _is_uuid(call_id) else uuid.uuid4()

    for tc in turn_result.tool_log:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO tool_calls (call_id, tool_name, input_json, output_json, latency_ms)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    call_uuid, tc["name"], json.dumps(tc["arguments"]), json.dumps(tc["output"]), turn_result.total_latency_ms,
                )
        except Exception as e:
            logger.warning(f"Failed to log tool call (non-fatal): {e}")
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE calls SET reasoning_mode = $1 WHERE id = $2", turn_result.final_tier, call_uuid
            )
    except Exception as e:
        logger.warning(f"Failed to record reasoning tier (non-fatal): {e}")


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


@app.post("/v1/chat/completions")
@app.post("/v1/chat/completions/{path_call_id}")
async def chat_completions(req: ChatCompletionRequest, path_call_id: str | None = None):
    """
    OpenAI-compatible endpoint. Agora's Conversational AI Engine calls this exactly like it would
    call OpenAI's API — same request/response shape — which is what makes "bring your own LLM"
    work without Agora needing to know anything about Groq/Gemini/our fallback chain underneath.

    IMPORTANT (confirmed against Agora's own docs, Day 2): Agora's Conversational AI Engine
    REQUIRES a streaming (SSE) response — it rejects non-streaming requests outright. This
    endpoint always streams, regardless of what the caller requests.

    Internally this runs the full tool-calling loop (see llm/agent_loop.py) — potentially several
    round-trips to Groq/Gemini and multiple tool executions — and only then emits a single SSE
    chunk carrying the complete final reply, followed by a stop chunk and [DONE]. This is
    deliberately NOT true token-by-token streaming from the upstream provider: Agora only requires
    protocol-compliant SSE framing, not that every token arrive separately, and building a single
    complete chunk keeps the tool-calling loop's correctness simple. If perceived latency during
    demos turns out to matter, a "please wait" filler chunk sent immediately (Agora's own
    documented pattern for RAG/slow lookups) is the next thing to add — not attempted in this
    first pass.

    call_id continuity: RE-RESOLVED. Two earlier attempts at a URL-based fix were reverted after
    appearing to break the endpoint entirely — but the real cause (confirmed via
    create_session(debug=True) dumping the actual outgoing request) was an unrelated stray space
    in the tunnel URL, now fixed with .strip() in agora_integration.py. With that fixed,
    agora_integration.py bakes call_id into the URL path Agora is given for this endpoint, so
    path_call_id below is the reliable source — every turn of a session is guaranteed to land
    under the same row. req.call_id (body) and a fresh UUID are kept only as fallbacks for
    direct/manual testing against this endpoint without going through invite_agent.
    """
    call_id = path_call_id or req.call_id or str(uuid.uuid4())
    call_uuid = uuid.UUID(call_id) if _is_uuid(call_id) else uuid.uuid4()
    call_id = str(call_uuid)  # normalize: guarantees run_agent_turn/execute_tool/_log_turn below
    # all write to the exact same row _ensure_call_row just created, even in the (currently
    # theoretical) case where Agora sends a non-UUID call_id string.

    # Moved BEFORE run_agent_turn (not just before _log_turn) — confirmed live that tool
    # execution itself (e.g. book_meeting writing to deal_sheets) can hit this FK before the
    # post-turn logging step ever runs, crashing the whole customer-facing response.
    await _ensure_call_row(call_uuid)

    messages = [m.model_dump(exclude_none=True) for m in req.messages]
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    completion_id = f"chatcmpl-{uuid.uuid4()}"
    created = int(time.time())
    model_name = req.model or "flowdeck-reasoning"

    async def event_stream():
        try:
            turn_result = await run_agent_turn(call_id, messages, req.tools or TOOLS)
            await _log_turn(call_id, turn_result)
            content = turn_result.content
        except Exception as e:
            # A failure here means every tier of our own fallback chain failed AND the rule-based
            # last resort raised too — extremely unlikely, but if it happens, don't just drop the
            # connection: send something the agent can speak instead of dead air.
            logger.error(f"Agent turn failed entirely: {type(e).__name__}: {e}")
            content = "I'm having trouble processing that right now — let me get someone from our team to help."

        content_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(content_chunk)}\n\n"

        stop_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(stop_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.on_event("shutdown")
async def shutdown():
    await close_pool()