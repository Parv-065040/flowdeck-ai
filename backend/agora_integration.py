"""
Agora Conversational AI integration — Day 2.

Three endpoints, matching the exact structure in Agora's own "Build a backend and client from
scratch" guide (docs.agora.io, updated 21 Jul 2026):
  - POST /api/token            issues RTC + RTM tokens for a channel/uid
  - POST /api/invite-agent     starts the conversational AI agent in a channel, pointed at OUR
                                /v1/chat/completions endpoint as its custom LLM
  - POST /api/stop-conversation  stops a running agent session by ID

This module is intentionally separate from main.py to keep the "pure backend logic" (reasoning,
tools, memory — provable with zero Agora dependency, per Day 1) cleanly split from the
"Agora-specific wiring" that can only really be tested against a live Agora account.

BEFORE THIS WORKS, you need:
  1. An Agora project with the Conversational AI (convoai) feature enabled — see docs/SETUP.md.
  2. AGORA_APP_ID and AGORA_APP_CERTIFICATE set in .env.
  3. A public HTTPS URL for this backend (Cloudflare Tunnel) — Agora's cloud cannot reach
     localhost. Set PUBLIC_BACKEND_URL in .env to that tunnel URL once it's running.

RESOLVED (was flagged unverified in the Day 2 handoff): checked `agora_agent.Area` directly inside
the container once the package was installed — available values are UNKNOWN, US, EU, AP, CN.
Using AP (Asia-Pacific) here for lower latency from India, instead of the US default shown in
Agora's own generic examples.
"""
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings
from tools.definitions import SYSTEM_PROMPT

logger = logging.getLogger("flowdeck.agora")
router = APIRouter()


@router.get("/api/config")
def get_public_config():
    """Added during Day 3 frontend work — the App ID (not the certificate) is safe to expose
    client-side; every real Agora web integration does this. Avoids the Day 2 test client's
    approach of a raw window.prompt() for the App ID, which isn't viable for a real product UI."""
    if not settings.agora_app_id:
        raise HTTPException(500, "AGORA_APP_ID not configured in .env")
    return {"appId": settings.agora_app_id}

TOKEN_TTL_SECONDS = 60 * 60  # 1 hour, matches Agora's documented example
AGENT_UID = "123456"  # must be a string — the installed agora-agents==2.7.2 SDK calls .isdigit()
# on this internally, which throws AttributeError on a plain int (confirmed via a live 502 during
# Day 2 testing: "start failed: 'int' object has no attribute 'isdigit'"). Kept as a numeric-looking
# string, distinct from any human participant's UID.


# ---------- token generation ----------
class TokenRequest(BaseModel):
    channel: str
    uid: int


@router.post("/api/token")
def generate_token(body: TokenRequest):
    """
    The only place AGORA_APP_CERTIFICATE is used — never send the certificate itself to the
    browser, only tokens derived from it. Matches Agora's documented Python example exactly.
    """
    if not settings.agora_app_id or not settings.agora_app_certificate:
        raise HTTPException(500, "AGORA_APP_ID / AGORA_APP_CERTIFICATE not configured in .env")

    try:
        from agora_token_builder import RtcTokenBuilder, RtmTokenBuilder
    except ImportError as e:
        raise HTTPException(500, f"agora-token-builder not installed: {e}")

    expire_at = int(time.time()) + TOKEN_TTL_SECONDS
    rtc_token = RtcTokenBuilder.buildTokenWithUid(
        settings.agora_app_id,
        settings.agora_app_certificate,
        body.channel,
        body.uid,
        role=1,  # publisher — matches Agora's documented example
        privilegeExpiredTs=expire_at,
    )
    rtm_token = RtmTokenBuilder.buildToken(
        settings.agora_app_id,
        settings.agora_app_certificate,
        str(body.uid),
        role=1,
        privilegeExpiredTs=expire_at,
    )
    return {"rtcToken": rtc_token, "rtmToken": rtm_token, "expireAt": expire_at}


# ---------- start the agent ----------
class InviteRequest(BaseModel):
    channel: str
    # Public URL of THIS backend, reachable by Agora's cloud (Cloudflare Tunnel). Required —
    # there's no safe default, since "reachable from Agora's servers" can never be localhost.
    public_backend_url: str
    # Optional: pass the call_id from a prior POST /calls/start so the deal sheet the frontend
    # is already polling gets populated by this exact session — see the call_id continuity fix
    # below. If omitted, a fresh one is generated and returned in the response.
    call_id: str | None = None


@router.post("/api/invite-agent")
def invite_agent(body: InviteRequest):
    if not settings.agora_app_id or not settings.agora_app_certificate:
        raise HTTPException(500, "AGORA_APP_ID / AGORA_APP_CERTIFICATE not configured in .env")
    if not body.public_backend_url or "localhost" in body.public_backend_url:
        raise HTTPException(
            400,
            "public_backend_url must be a real public URL (e.g. your Cloudflare Tunnel URL) — "
            "Agora's cloud cannot reach localhost.",
        )

    try:
        from agora_agent import Agora, Agent, Area, CustomLLM, DeepgramSTT, MiniMaxTTS, expires_in_hours
    except ImportError as e:
        raise HTTPException(
            500,
            f"agora_agent import failed: {e}. NOTE: pip package is 'agora-agents' (plural) but "
            "the import is 'agora_agent' (singular) — this mismatch is documented as-written in "
            "Agora's own guide, confirmed during this build. If this error persists after "
            "confirming the package is installed, check Agora's current docs for a naming change.",
        )

    agora_client = Agora(
        area=Area.AP,  # Asia-Pacific — confirmed available via `list(Area)`, better than US for India
        app_id=settings.agora_app_id,
        app_certificate=settings.agora_app_certificate,
    )

    # REVERTED (Day 4, second attempt): the path-segment approach below was a guess made without
    # a live test, intended to fix a suspected-but-unconfirmed query-string issue. After it
    # shipped, EVERY subsequent live test showed zero /v1/chat/completions requests ever
    # reaching this backend — not even failed ones — despite Day 2's plain, unmodified
    # `/v1/chat/completions` working correctly with real Groq replies. The most likely
    # RESOLVED: the earlier "zero calls ever reach our endpoint" mystery (which caused two
    # premature reverts of this call_id-in-path approach) had nothing to do with URL structure —
    # it was a stray trailing space in body.public_backend_url from copy-pasting the tunnel URL,
    # confirmed directly via create_session(debug=True) dumping the actual outgoing payload. With
    # .strip() now in place below, the path-segment approach is safe to use again: it's the
    # correct fix for call_id continuity (Agora invents its own call_id/turn_id per turn rather
    # than echoing one back), and baking it into the URL guarantees every turn of a session
    # lands under the same row regardless of what the request body does or doesn't contain.
    call_id = body.call_id or str(uuid.uuid4())
    llm_endpoint = body.public_backend_url.strip().rstrip("/") + f"/v1/chat/completions/{call_id}"

    agent = (
        Agent(
            agora_client,
            instructions=SYSTEM_PROMPT,
            greeting="Hi, this is Rae from Flowdeck — thanks for calling in. How can I help today?",
            failure_message="Sorry, I had trouble hearing that. Could you say that again?",
            max_history=50,
            # enable_tools=True is REQUIRED for our tool-calling to work — Agora's own default
            # example has this False, which would silently break everything if copied as-is.
            advanced_features={"enable_rtm": True, "enable_tools": True},
            parameters={"data_channel": "rtm", "enable_error_message": True},
        )
        # Agora-managed STT/TTS presets (per Section 5 of PRODUCTION_PLAN.md) — no vendor API
        # keys needed for these; only our own LLM is "bring your own."
        .with_stt(DeepgramSTT(model="nova-3", language="en"))
        .with_llm(
            CustomLLM(
                api_key="",  # our endpoint has no auth of its own yet — fine for a hackathon demo
                base_url=llm_endpoint,
                model="flowdeck-reasoning",
                system_messages=[{"role": "system", "content": SYSTEM_PROMPT}],
            )
        )
        .with_tts(MiniMaxTTS(model="speech_2_6_turbo", voice_id="English_captivating_female1"))
    )

    session = agent.create_session(
        channel=body.channel,
        agent_uid=AGENT_UID,
        remote_uids=["*"],
        name="flowdeck-sales-agent",
        idle_timeout=30,
        expires_in=expires_in_hours(1),
    )

    try:
        result = session.start()
        # SDK version drift, confirmed live during Day 2 testing (agora-agents==2.7.2): some
        # versions/paths return an object with an `.agent_id` attribute (matches Agora's own
        # docs), others return the agent ID as a plain string directly. Handle both rather than
        # assuming one — this cost a real 502 ("'str' object has no attribute 'agent_id'") before
        # being made defensive.
        resolved_agent_id = result.agent_id if hasattr(result, "agent_id") else result
        logger.info(f"Agent started: agent_id={resolved_agent_id} channel={body.channel} call_id={call_id}")
        return {"agentId": resolved_agent_id, "agentUid": AGENT_UID, "callId": call_id}
    except Exception as exc:
        logger.error(f"Agent start failed: {exc}")
        raise HTTPException(status_code=502, detail=f"start failed: {exc}")


# ---------- stop the agent ----------
class StopRequest(BaseModel):
    agent_id: str


@router.post("/api/stop-conversation")
def stop_conversation(body: StopRequest):
    try:
        from agora_agent import Agora, Area
    except ImportError as e:
        raise HTTPException(500, f"agora_agent import failed: {e}")

    agora_client = Agora(
        area=Area.AP,  # must match the area used in invite_agent, or the session may not be found
        app_id=settings.agora_app_id,
        app_certificate=settings.agora_app_certificate,
    )
    try:
        # Confirmed live during Day 2 testing: agents.leave(agent_id) does not exist on this
        # SDK version's client ("'AgentsClient' object has no attribute 'leave'"). The correct
        # method, per Agora's own SDK docs for the stateless-server pattern (start in one
        # request, stop by ID in a later request — exactly our case), is stop_agent(agent_id)
        # directly on the Agora client, not a sub-attribute.
        agora_client.stop_agent(body.agent_id)
        return {"stopped": True}
    except Exception as exc:
        logger.error(f"Agent stop failed: {exc}")
        raise HTTPException(status_code=502, detail=f"stop failed: {exc}")