"""Central Kokoro voice blocklist (config/voice_blocklist.json).

Every place that picks or sends a Kokoro voice should go through here (Python) or
``packages/mcp-server/src/kokoro/voicePolicy.ts`` (TypeScript, applied to every
synth request). Blocked voices are filtered from pools/rotation/UI lists; an explicit
request for one is swapped for a same-gender/accent substitute with a warning.
"""
from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "voice_blocklist.json"
_DEFAULT = {
    "blocked": ["am_adam"],
    "blocked_name_substrings": ["adam"],
    "substitutes": {"am_adam": "am_michael"},
    "fallback_by_prefix": {"am": "am_michael", "bm": "bm_george", "af": "af_bella", "bf": "bf_emma"},
    "fallback": "am_michael",
}
_COMPONENT_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*(\([^)]*\))?\s*$")


@lru_cache(maxsize=1)
def policy() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("kokoro") or {}
    except (OSError, ValueError):
        data = {}
    merged = dict(_DEFAULT)
    merged.update({k: v for k, v in data.items() if v is not None})
    # The built-in Adam entry can never be dropped by a broken/empty config.
    merged["blocked"] = sorted({*(_DEFAULT["blocked"]), *(merged.get("blocked") or [])})
    merged["blocked_name_substrings"] = sorted({*(_DEFAULT["blocked_name_substrings"]),
                                                *(merged.get("blocked_name_substrings") or [])})
    return merged


def _base(component: str) -> str:
    m = _COMPONENT_RE.match(component or "")
    return (m.group(1) if m else (component or "").strip()).lower()


def _component_blocked(component: str) -> bool:
    base = _base(component)
    if not base:
        return False
    p = policy()
    if base in {b.lower() for b in p["blocked"]}:
        return True
    name = base.split("_", 1)[1] if "_" in base else base
    return any(s.lower() in name for s in p["blocked_name_substrings"])


def is_blocked(voice: str | None) -> bool:
    """True if the voice (or any component of a '+' blend) is blocked."""
    if not voice:
        return False
    return any(_component_blocked(c) for c in str(voice).split("+"))


def substitute_for(voice: str) -> str:
    p = policy()
    base = _base(voice)
    sub = (p.get("substitutes") or {}).get(base)
    if sub and not is_blocked(sub):
        return sub
    prefix = base.split("_", 1)[0] if "_" in base else ""
    sub = (p.get("fallback_by_prefix") or {}).get(prefix)
    if sub and not is_blocked(sub):
        return sub
    return p.get("fallback") or "am_michael"


def enforce_voice(voice: str | None, *, context: str = "", warn: bool = True) -> str | None:
    """Return a non-blocked voice id; blocked parts are swapped for substitutes."""
    if not voice or not is_blocked(voice):
        return voice
    out: list[str] = []
    for comp in str(voice).split("+"):
        if _component_blocked(comp):
            m = _COMPONENT_RE.match(comp)
            weight = (m.group(2) or "") if m else ""
            comp = substitute_for(comp) + weight
        if _base(comp) not in {_base(c) for c in out}:
            out.append(comp.strip())
    fixed = "+".join(out)
    if warn:
        where = f" ({context})" if context else ""
        print(f"[voices] WARNING: Kokoro voice '{voice}' is blocklisted{where}; using '{fixed}' instead.",
              file=sys.stderr)
    return fixed


def filter_voices(voices: Iterable[str]) -> list[str]:
    """Drop blocked ids (or labels like 'am_adam (Male)') from a pool/UI list."""
    return [v for v in voices if not is_blocked(str(v).split(" ", 1)[0])]


def enforce_episode_hosts(episode: dict[str, Any], *, context: str = "episode") -> list[tuple[str, str]]:
    """Swap blocked host voice_ids in-place. Returns [(old, new), ...]."""
    changes: list[tuple[str, str]] = []
    for h in (episode or {}).get("hosts") or []:
        vid = h.get("voice_id")
        if isinstance(vid, str) and is_blocked(vid):
            new = enforce_voice(vid, context=f"{context} host {h.get('id')}")
            h["voice_id"] = new
            changes.append((vid, new))
    return changes
