# Flowdeck AI — Frontend

React + Tailwind product UI. This is the real product interface — not a test harness (that's
`demo/agora_test_client.html` at the repo root, kept only for reference/fallback testing).

## Structure

```
frontend/
├── src/
│   ├── main.jsx
│   ├── App.jsx        # Orchestrates the whole call lifecycle + UI state
│   ├── index.css      # Tailwind base + custom design tokens
│   └── lib/
│       ├── api.js     # Backend API client (token, invite-agent, deal-sheet, etc.)
│       └── agora.js   # Agora RTC wrapper: join/publish/leave, volume indicator,
│                       # and the audio-playback-blocked recovery fix
├── public/
│   ├── favicon.svg
│   └── icons.svg
├── index.html
├── vite.config.js
└── tailwind.config.js
```

## Running locally

```bash
npm install
npm run dev
```

Before starting a call in the UI, paste the current `cloudflared` tunnel URL into the
**"Public backend URL"** field. This URL changes on every `cloudflared` restart (free tier),
so re-paste it each session.

## Notable implementation details

- **Audio autoplay recovery:** browsers can silently block the agent's spoken replies under
  autoplay policy, with no visible error — indistinguishable from "the agent isn't replying."
  `lib/agora.js` catches the playback promise rejection and surfaces a visible "click to enable
  audio" recovery button instead of failing silently.
- **URL trimming:** the "Public backend URL" field is trimmed on blur and again immediately
  before use in the API call, to guard against a trailing-space bug that previously broke the
  Agora BYO-LLM endpoint URL (see backend README / root docs for the full story).
- **Live "deal sheet" panel:** polls/subscribes to backend call data keyed by `call_id` and
  renders contact name, use case, budget signal, competitor mentions, and next action as the
  call progresses.

## Live transcript (not currently wired in)

A live transcript panel was built via `agora-agent-client-toolkit` but removed before submission
— it consistently failed to initialize and there wasn't time to debug it. The voice call itself
never depended on it. If picking this back up:

- Correct package: `agora-agent-client-toolkit` (npm), class `AgoraVoiceAI` — not
  `ConversationalAIAPI` as Agora's docs describe. RTM is nested as
  `{ rtcEngine, rtmConfig: { rtmEngine } }`.
- `RTMClient` constructor is positional: `new RTMClient(appId, userId, config?)` (per
  `agora-rtm-sdk`'s own `.d.ts`, not online tutorials).
- Wiring is RTM login → `AgoraVoiceAI.init()` → `subscribeMessage(channel)` →
  `TRANSCRIPT_UPDATED` event, wrapped in try/catch so a failure there can't break the voice call.
  `AgoraVoiceAI.init()` (or something immediately after) throws — the real error was never
  captured before time ran out. First step on retry: restore the `onTranscriptError` wiring to
  push the actual caught error to the Activity log and go from there.
