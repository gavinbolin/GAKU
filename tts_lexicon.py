"""tts_lexicon.py -- pronunciation respellings applied to TTS input only.

Registry: podcast-pipeline/config/tts_lexicon.json (see docs/tts-lexicon.md).

* `apply_lexicon(text)` -> the string actually spoken (used by tests; the
  live TTS path applies the same rules in generate_audio.ts).
* `collapse_spoken_tokens(aligned)` maps whisper words that came from a
  respelling back to the written form so karaoke keeps showing the correct
  spelling with correct timing (script-anchored alignment then matches it).

Matching rules (identical in TS): whole word, case-insensitive, keeps a
trailing possessive / punctuation, keeps the source capitalization style
(ALLCAPS stays ALLCAPS, Capitalized stays Capitalized).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

LEXICON_PATH = Path(__file__).resolve().parent / "config" / "tts_lexicon.json"


# written(lower) -> whisper spellings of the respelling (optional "heard_as").
_HEARD: dict[str, tuple[str, ...]] = {}


@lru_cache(maxsize=4)
def _load(path_str: str) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    path = Path(path_str)
    if not path.exists():
        return tuple()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return tuple()
    out = []
    for e in data.get("entries") or []:
        w = str(e.get("written") or "").strip()
        s = str(e.get("spoken") or "").strip()
        if not w or not s or w == s:
            continue
        backends = tuple(str(b).lower() for b in (e.get("backends") or []))
        out.append((w, s, backends))
        _HEARD[w.lower()] = tuple(str(h) for h in (e.get("heard_as") or []) if str(h).strip())
    # Longest written form first so multi-word entries win.
    out.sort(key=lambda t: len(t[0]), reverse=True)
    return tuple(out)


def entries(path: Path | None = None) -> list[tuple[str, str, tuple[str, ...]]]:
    return list(_load(str(path or LEXICON_PATH)))


def _match_case(src: str, repl: str) -> str:
    if src.isupper() and len(src) > 1:
        return repl.upper()
    if src[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl.lower()


def apply_lexicon(text: str, backend: str | None = None, path: Path | None = None) -> str:
    out = text or ""
    for written, spoken, backends in entries(path):
        if backend and backends and backend.lower() not in backends:
            continue
        pat = re.compile(r"(?<![\w-])" + re.escape(written) + r"(?![\w])", re.IGNORECASE)
        out = pat.sub(lambda m: _match_case(m.group(0), spoken), out)
    return out


def _norm(tok: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (tok or "").lower())


def spoken_to_written_norm(path: Path | None = None) -> dict[tuple[str, ...], str]:
    """{(normalized spoken tokens...): normalized written token}."""
    out: dict[tuple[str, ...], str] = {}
    for written, spoken, _b in entries(path):
        sp = tuple(t for t in (_norm(x) for x in re.split(r"[\s-]+", spoken)) if t)
        wr = _norm(written)
        if sp and wr:
            out[sp] = wr
            joined = ("".join(sp),)
            out.setdefault(joined, wr)
            # Whisper often spells the respelling differently ("Pahlitzer" ->
            # "Palitzer"); fold those back too. Never map a real different
            # word unless the entry lists it explicitly in heard_as.
            for h in _HEARD.get(written.lower(), ()):
                ht = tuple(t for t in (_norm(x) for x in re.split(r"[\s-]+", h)) if t)
                if ht:
                    out.setdefault(ht, wr)
    return out


def collapse_spoken_tokens(aligned: list[dict[str, Any]], path: Path | None = None) -> list[dict[str, Any]]:
    """Merge whisper tokens that spell a lexicon respelling into one token
    carrying the written form (start of first, end of last)."""
    table = spoken_to_written_norm(path)
    if not table or not aligned:
        return aligned
    max_n = max(3, max(len(k) for k in table))
    out: list[dict[str, Any]] = []
    i = 0
    n = len(aligned)
    while i < n:
        merged = False
        for size in range(min(max_n, n - i), 0, -1):
            key = tuple(_norm(aligned[i + k].get("word") or "") for k in range(size))
            hit = table.get(key)
            if hit is None and size > 1 and all(key):
                hit = table.get(("".join(key),))  # whisper split "Pah litzer"
            if hit is not None:
                first, last = aligned[i], aligned[i + size - 1]
                out.append({
                    **first,
                    "word": hit,
                    "start_ms": int(first.get("start_ms", 0)),
                    "end_ms": int(last.get("end_ms", first.get("end_ms", 0))),
                })
                i += size
                merged = True
                break
        if not merged:
            out.append(aligned[i])
            i += 1
    return out


def spoken_episode(episode: dict[str, Any], backend: str | None = None) -> dict[str, Any]:
    """Deep copy of an episode with TTS respellings applied to line text."""
    import copy

    ep = copy.deepcopy(episode)
    for seg in ep.get("segments") or []:
        for line in seg.get("lines") or []:
            if isinstance(line.get("text"), str):
                line["text"] = apply_lexicon(line["text"], backend)
    return ep


__all__ = ["apply_lexicon", "collapse_spoken_tokens", "spoken_episode", "entries", "LEXICON_PATH"]
