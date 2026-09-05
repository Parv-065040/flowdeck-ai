"""
Central configuration. Everything here is read from environment variables so nothing
is ever hardcoded — see .env.example at the repo root for the full list with explanations.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://flowdeck:flowdeck_dev_password@localhost:5432/flowdeck"
    qdrant_url: str = "http://localhost:6333"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b-instruct-q4_K_M"  # kept as an optional 4th tier — see docs/SETUP.md
    ollama_fallback_model: str = "llama3.2:3b-instruct-q4_K_M"
    ollama_timeout_seconds: float = 20.0
    ollama_enabled: bool = False  # off by default now that Groq/Gemini are the primary chain

    # Primary reasoning tier. GPT-OSS 120B via Groq: ~500 tokens/sec, matches/beats o4-mini on many
    # benchmarks, free tier is 1,000 requests/day + 30/min — verified current as of this build (Groq
    # deprecated its Llama models in June 2026; don't use llama-3.1-8b-instant, it's gone).
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: float = 8.0

    # Secondary tier — different provider from Groq on purpose, so a Groq outage doesn't take the
    # whole reasoning layer down with it. Verify the exact OpenAI-compatible endpoint path against
    # Google's current docs before Day 2 — this moves fast enough that it's worth a quick check.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"  # gemini-2.5-flash was retired for new users — confirmed
    # directly from Google's own API error response during Day 1 testing, not a guess.
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_timeout_seconds: float = 10.0

    google_calendar_credentials_json: str = ""
    google_calendar_id: str = "primary"

    agora_app_id: str = ""
    agora_app_certificate: str = ""

    log_level: str = "info"

    class Config:
        env_file = ".env"


settings = Settings()
