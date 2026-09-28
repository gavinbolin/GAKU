"""Shared Gemini JSON helper with key rotation + model fallback (2026-09-28).

Why: the M7 render-time fact-check hit the free-tier daily quota on the one
configured model (ResourceExhausted, gemini-3.6-flash 20/day) and silently
returned checked_claims=0. This helper walks MODELS x KEYS, honours
"retry in Ns" hints for per-minute quota (<= 65 s), skips straight to the
next key/model for per-day quota or unknown models, and raises
GeminiUnavailable (with every attempt recorded) only when all combos fail.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Callable

DEFAULT_FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.8-flash",       # suggested by the API's 404 for retired 2.5 models (2026-09-28)
    "gemini-2.5-flash",
    "gemini-3.5-flash-lite",  # suggested replacement for 2.5-flash-lite
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
]
KEY_ENV_NAMES = ("GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY")
MAX_RETRY_WAIT_S = 65.0


class GeminiUnavailable(RuntimeError):
    def __init__(self, message: str, attempts: list[dict[str, Any]]):
        super().__init__(message)
        self.attempts = attempts


def gemini_keys() -> list[str]:
    seen: list[str] = []
    for name in KEY_ENV_NAMES:
        v = (os.environ.get(name) or "").strip()
        if v and v not in seen:
            seen.append(v)
    return seen


def gemini_models(primary: str | None = None) -> list[str]:
    out: list[str] = []
    for m in [primary, os.environ.get("GEMINI_MODEL"), *DEFAULT_FALLBACK_MODELS]:
        m = (m or "").strip()
        if m and m not in out:
            out.append(m)
    return out


_RETRY_RE = re.compile(r"retry(?:_delay)?\s*(?:in|\{)?\s*(?:seconds:)?\s*([\d.]+)\s*s?", re.I)


def classify_error(exc: BaseException) -> tuple[str, float | None]:
    """-> (kind, retry_after_s). kind in quota_minute|quota_day|not_found|other."""
    msg = f"{type(exc).__name__}: {exc}"
    low = msg.lower()
    m = _RETRY_RE.search(msg)
    delay = float(m.group(1)) if m else None
    if "resourceexhausted" in low or "429" in low or "quota" in low or "rate limit" in low:
        if "perday" in low or "per_day" in low or "per day" in low or "requests_per_day" in low:
            return "quota_day", delay
        return "quota_minute", delay
    if "notfound" in low or "404" in low or "not found" in low or "is not supported" in low:
        return "not_found", None
    return "other", None


def _strip_fences(raw: str) -> str:
    raw = (raw or "").strip()
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.M).strip()


def generate_json(
    prompt: str,
    *,
    models: list[str] | None = None,
    keys: list[str] | None = None,
    temperature: float = 0.1,
    max_output_tokens: int = 16384,
    validate: Callable[[Any], bool] | None = None,
    log: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    call: Callable[[str, str, str, dict[str, Any]], str] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Return (parsed_json, meta). meta = {model, key_index, attempts}."""
    log = log or (lambda s: print(f"[gemini] {s}", file=sys.stderr))
    keys = keys if keys is not None else gemini_keys()
    models = models or gemini_models()
    attempts: list[dict[str, Any]] = []
    if not keys:
        raise GeminiUnavailable("no GEMINI_API_KEY* set", attempts)
    cfg = {"temperature": temperature, "response_mime_type": "application/json",
           "max_output_tokens": max_output_tokens}

    def _default_call(key: str, model_name: str, text: str, gen_cfg: dict[str, Any]) -> str:
        import google.generativeai as genai
        genai.configure(api_key=key)
        return genai.GenerativeModel(model_name).generate_content(text, generation_config=gen_cfg).text or ""

    call = call or _default_call
    for model_name in models:
        waited_on_model = False  # wait at most once per model; a 2nd 429 after waiting ~= daily cap
        for ki, key in enumerate(keys):
            for retry in range(2):  # one wait-and-retry for per-minute quota
                try:
                    raw = call(key, model_name, prompt, cfg)
                    parsed = json.loads(_strip_fences(raw))
                    if validate is not None and not validate(parsed):
                        raise ValueError("response failed validation")
                    attempts.append({"model": model_name, "key": ki, "ok": True})
                    return parsed, {"model": model_name, "key_index": ki, "attempts": attempts}
                except Exception as exc:  # noqa: BLE001
                    kind, delay = classify_error(exc)
                    attempts.append({"model": model_name, "key": ki, "ok": False, "kind": kind,
                                     "error": f"{type(exc).__name__}: {str(exc)[:240]}"})
                    log(f"{model_name} key#{ki} failed ({kind}): {str(exc)[:160]}")
                    if kind == "not_found":
                        # 404 can be per-key/project ("no longer available to new
                        # users"), so try the remaining keys before giving up.
                        break
                    if kind == "quota_minute" and retry == 0 and not waited_on_model:
                        waited_on_model = True
                        wait = min(MAX_RETRY_WAIT_S, (delay or 20.0) + 1.0)
                        log(f"per-minute quota; waiting {wait:.0f}s then retrying {model_name}")
                        sleep(wait)
                        continue
                    break  # quota_day / other / second failure -> next key
    raise GeminiUnavailable(f"all Gemini models/keys failed ({len(attempts)} attempts)", attempts)


__all__ = ["GeminiUnavailable", "generate_json", "gemini_keys", "gemini_models", "classify_error"]
