"""
groq_client.py — REBUILT
Single consolidated entry point for all Groq API calls.
All other files call this module. No raw requests.post() to Groq anywhere else.

Fixes addressed:
- Retry logic with exponential backoff on 429 rate limit errors
- Single source of truth for model, URL, and key resolution
- stagger_delay parameter for parallel call coordination
- Graceful fallback messaging instead of silent failure
"""

import os
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

# Look in root project dir (parent of core/) for .env
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path, override=True)

GROQ_API_URL  = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL    = "llama-3.1-8b-instant"
MAX_RETRIES   = 3
RETRY_BASE_DELAY = 2.0


def get_groq_key() -> str:
    return os.getenv("GROQ_API_KEY", "")


def is_available() -> bool:
    return bool(get_groq_key())


def chat_completion(
    messages: list,
    temperature: float = 0.6,
    max_tokens: int = 1200,
    retries: int = MAX_RETRIES,
    stagger_delay: float = 0.0,
    timeout: int = 30
) -> str:
    """
    Single entry point for all Groq API calls across the entire codebase.

    Parameters:
        messages       : OpenAI-format message list [{"role": ..., "content": ...}]
        temperature    : 0.0–1.0. Lower = more deterministic.
        max_tokens     : Max output tokens.
        retries        : Number of retry attempts on failure or rate limit.
        stagger_delay  : Seconds to wait before making the call (for parallel staggering).
        timeout        : Request timeout in seconds.

    Returns:
        Stripped string content from the model response.

    Raises:
        RuntimeError if all retries are exhausted or no API key is found.
    """
    if stagger_delay > 0:
        time.sleep(stagger_delay)

    key = get_groq_key()
    if not key:
        raise RuntimeError("GROQ_API_KEY not found in environment variables.")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json"
    }
    payload = {
        "model":      GROQ_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages":   messages
    }

    last_error = None
    for attempt in range(retries):
        try:
            response = requests.post(
                GROQ_API_URL, headers=headers, json=payload, timeout=timeout
            )

            if response.status_code == 429:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"  [ GROQ ] Rate limited. Retrying in {delay:.1f}s "
                      f"(attempt {attempt + 1}/{retries})")
                time.sleep(delay)
                last_error = "429 rate limit"
                continue

            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()

        except requests.exceptions.Timeout:
            last_error = "Request timed out"
            if attempt < retries - 1:
                time.sleep(RETRY_BASE_DELAY)

        except Exception as e:
            last_error = str(e)
            if attempt < retries - 1:
                time.sleep(RETRY_BASE_DELAY * (attempt + 1))

    raise RuntimeError(
        f"Groq call failed after {retries} attempts. Last error: {last_error}"
    )
