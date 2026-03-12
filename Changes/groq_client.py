"""
groq_client.py — AUDITED & CORRECTED
Single consolidated entry point for all Groq API calls.
All other files call this module. No raw requests.post() to Groq anywhere else.

Fixes from original build:
- Retry logic with exponential backoff on 429 rate limit errors
- Single source of truth for model, URL, and key resolution
- stagger_delay parameter for parallel call coordination
- Graceful fallback messaging instead of silent failure

Fixes from audit pass:
- GROQ_MODEL now reads from .env (GROQ_MODEL key) with hardcoded string as fallback
- Added jitter to retry delays — prevents thundering herd when parallel persona
  calls all hit rate limit simultaneously and retry at identical intervals
- Key validation moved before stagger_delay sleep — no point sleeping if there's no key
- HTTP error response body now captured before raise_for_status() — Groq's actual
  error message (model overloaded, bad request, etc.) is now visible in exceptions
- Optional model parameter added to chat_completion() — per-call model override
  for future branch architecture (school / rehab / personal may use different tiers)
- 503 / 502 status codes now retried — Groq returns these during high traffic;
  original code threw immediately on first attempt without retrying
- Optional token usage logging via MARLOW_LOG_USAGE env flag — surfaces prompt/
  completion/total token counts per call for TPM budget debugging on free tier
"""

import os
import time
import random
import requests
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(env_path, override=True)

GROQ_API_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL       = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
MAX_RETRIES      = 3
RETRY_BASE_DELAY = 2.0

# Status codes that should be retried rather than thrown immediately
# 429 = rate limit | 503 = model overloaded | 502 = bad gateway
_RETRYABLE_STATUS = {429, 502, 503}

# Set MARLOW_LOG_USAGE=1 in .env to print token counts after every call
_LOG_USAGE = os.getenv("MARLOW_LOG_USAGE", "0") == "1"


def get_groq_key() -> str:
    return os.getenv("GROQ_API_KEY", "")


def is_available() -> bool:
    return bool(get_groq_key())


def _log_token_usage(label: str, usage: dict):
    """
    Prints token usage from a Groq response object.
    Only fires when MARLOW_LOG_USAGE=1 is set in .env.
    Useful for diagnosing TPM rate limit hits on the free tier.
    """
    if not _LOG_USAGE or not usage:
        return
    prompt     = usage.get("prompt_tokens", "?")
    completion = usage.get("completion_tokens", "?")
    total      = usage.get("total_tokens", "?")
    print(f"  [ GROQ ] {label} — "
          f"prompt: {prompt} | completion: {completion} | total: {total}")


def chat_completion(
    messages: list,
    temperature: float = 0.6,
    max_tokens: int = 1200,
    retries: int = MAX_RETRIES,
    stagger_delay: float = 0.0,
    timeout: int = 30,
    model: str = None,
    _label: str = "call"
) -> str:
    """
    Single entry point for all Groq API calls across the entire codebase.

    Parameters:
        messages       : OpenAI-format message list [{"role": ..., "content": ...}]
        temperature    : 0.0-1.0. Lower = more deterministic.
        max_tokens     : Max output tokens.
        retries        : Number of retry attempts on failure or rate limit.
        stagger_delay  : Seconds to wait before making the call (for parallel staggering).
        timeout        : Request timeout in seconds.
        model          : Override the global GROQ_MODEL for this specific call.
                         If None, uses the module-level GROQ_MODEL constant.
        _label         : Internal identifier for token usage log output.

    Returns:
        Stripped string content from the model response.

    Raises:
        RuntimeError if all retries are exhausted or no API key is found.
    """
    # Validate key BEFORE sleeping on stagger_delay — no point waiting if no key
    key = get_groq_key()
    if not key:
        raise RuntimeError("GROQ_API_KEY not found in environment variables.")

    if stagger_delay > 0:
        time.sleep(stagger_delay)

    active_model = model if model else GROQ_MODEL

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json"
    }
    payload = {
        "model":       active_model,
        "max_tokens":  max_tokens,
        "temperature": temperature,
        "messages":    messages
    }

    last_error = None
    for attempt in range(retries):
        try:
            response = requests.post(
                GROQ_API_URL, headers=headers, json=payload, timeout=timeout
            )

            # Retryable HTTP errors — 429 rate limit, 502 bad gateway, 503 overloaded
            if response.status_code in _RETRYABLE_STATUS:
                base_delay  = RETRY_BASE_DELAY * (2 ** attempt)
                jitter      = random.uniform(0.0, 1.0)
                delay       = base_delay + jitter

                status_label = {
                    429: "Rate limited",
                    502: "Bad gateway",
                    503: "Model overloaded"
                }.get(response.status_code, f"HTTP {response.status_code}")

                print(f"  [ GROQ ] {status_label}. Retrying in {delay:.1f}s "
                      f"(attempt {attempt + 1}/{retries})")
                time.sleep(delay)
                last_error = f"HTTP {response.status_code}"
                continue

            # Non-retryable HTTP error — capture Groq body before raising
            if not response.ok:
                try:
                    error_body = response.json()
                    groq_msg   = error_body.get("error", {}).get("message", response.text)
                except Exception:
                    groq_msg = response.text
                last_error = f"HTTP {response.status_code}: {groq_msg}"
                if attempt < retries - 1:
                    delay = RETRY_BASE_DELAY * (attempt + 1) + random.uniform(0.0, 0.5)
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"Groq API error — {last_error}")

            data    = response.json()
            content = data["choices"][0]["message"]["content"].strip()

            _log_token_usage(_label, data.get("usage", {}))

            return content

        except RuntimeError:
            raise

        except requests.exceptions.Timeout:
            last_error = "Request timed out"
            if attempt < retries - 1:
                delay = RETRY_BASE_DELAY + random.uniform(0.0, 0.5)
                time.sleep(delay)

        except Exception as e:
            last_error = str(e)
            if attempt < retries - 1:
                delay = RETRY_BASE_DELAY * (attempt + 1) + random.uniform(0.0, 0.5)
                time.sleep(delay)

    raise RuntimeError(
        f"Groq call failed after {retries} attempts. Last error: {last_error}"
    )
