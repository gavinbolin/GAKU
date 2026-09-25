"""text_clean.py -- strip markdown emphasis from spoken script text.

Gemini sometimes writes emphasis like *build*, **really**, _this_ or `code`
into dialogue. Those characters must never reach the TTS engine or the
karaoke captions (2026-09-25 review: '*build*' shown on screen).

`strip_markdown(text)` removes the markers but keeps every word, so the
number and order of words is unchanged and word-timing alignment still maps
1:1. The emphasized word renders like any other word. Mirrored in
packages/mcp-server/src/tools/generate_audio.ts (stripMarkdown).
"""

from __future__ import annotations

import re
from typing import Any

_EMPHASIS = [
    re.compile(r"(?<![\w*])\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*(?![\w*])"),
    re.compile(r"(?<![\w*])\*\*(?=\S)(.+?)(?<=\S)\*\*(?![\w*])"),
    re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])"),
    re.compile(r"(?<![\w_])__(?=\S)(.+?)(?<=\S)__(?![\w_])"),
    re.compile(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])"),
]
# Leftover unpaired markers at word edges ("*build", "really**", "_note").
_EDGE_MARKERS = re.compile(r"(?<!\S)[*_]+(?=\S)|(?<=\S)[*_]+(?!\S)")
_SPACES = re.compile(r"[ \t]{2,}")


def strip_markdown(text: str) -> str:
    if not text:
        return text or ""
    s = text.replace("`", "")
    if "*" not in s and "_" not in s:
        return s
    for pat in _EMPHASIS:
        s = pat.sub(r"\1", s)
    s = _EDGE_MARKERS.sub("", s)
    s = s.replace("*", "")  # asterisks are never spoken
    s = _SPACES.sub(" ", s).strip()
    return s


_CARD_FIELDS = ("title", "subtitle", "label", "subtext", "caption", "quote", "term", "definition")


def clean_episode_markdown(episode: dict[str, Any]) -> int:
    """Strip markdown in place from line text (TTS + captions) and simple card
    text fields. Returns the number of strings changed."""
    changed = 0
    for seg in (episode or {}).get("segments") or []:
        for line in seg.get("lines") or []:
            t = line.get("text")
            if isinstance(t, str):
                c = strip_markdown(t)
                if c != t:
                    line["text"] = c
                    changed += 1
        for group in ("overlays", "visuals"):
            for item in seg.get(group) or []:
                if not isinstance(item, dict):
                    continue
                targets = [item] + ([item["data"]] if isinstance(item.get("data"), dict) else [])
                for obj in targets:
                    for k in _CARD_FIELDS:
                        v = obj.get(k)
                        if isinstance(v, str):
                            c = strip_markdown(v)
                            if c != v:
                                obj[k] = c
                                changed += 1
                    items = obj.get("items")
                    if isinstance(items, list):
                        new = [strip_markdown(x) if isinstance(x, str) else x for x in items]
                        if new != items:
                            obj["items"] = new
                            changed += 1
    return changed


__all__ = ["strip_markdown", "clean_episode_markdown"]
