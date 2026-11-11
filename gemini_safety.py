"""Opt-in fail-fast policy for explicitly quota/billing-sensitive runs."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import sys

SIGNAL = re.compile(r"quota|resource[ _]?exhausted|rate.?limit|too many requests|billing|payment|paid[ _-]?tier|charges?|charged|chargeable", re.I)

def enabled() -> bool:
    return os.environ.get("GAKU_GEMINI_STOP_ON_QUOTA", "").strip().lower() in {"1", "true", "yes", "on"}

def stop_on_gemini_budget_error(exc: BaseException) -> None:
    if not enabled():
        return
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status not in (401, 402, 403, 429) and not SIGNAL.search(f"{type(exc).__name__}: {exc}"):
        return
    marker = os.environ.get("GAKU_GEMINI_STOP_FILE")
    if marker:
        Path(marker).write_text(json.dumps({"stopped": True, "runtime": "python", "reason": "quota_billing_or_auth_signal", "status": status if isinstance(status, int) else None}), encoding="utf-8")
    print("[gemini-safety] Quota/billing/auth signal: stopped without retry or key/model rotation.", file=sys.stderr)
    raise SystemExit(85)
