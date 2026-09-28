"""visual_timeline.py -- in-segment beat planner (approved 2026-09-25 review).

ROOT CAUSE this module fixes: visual_queue's "Segment Blocks" manifest only
emitted one static SegmentCard per segment and never consumed the script's
per-segment `visuals` array (planned TitleCards / StatCallouts / diagrams).
Overlays were passed through, but a long bullets overlay (e.g. 11s-237s)
masked every stat inside it because SegmentCard shows only the FIRST active
overlay, and B-roll came from a Gemini-only window loop that silently
skipped every failed window.

This planner turns script + research data into a non-overlapping beat
timeline per SegmentCard:

  chapter  -- mid-episode chapter title (hook + subtitle), ~5s
  stat     -- one big StatHero per unique key stat (deduped across the episode)
  diagram  -- flow / hub / pillars / comparison / timeline from the script
  bullets  -- remaining bullet cards, capped ~8-14s
  chart    -- ChartOverlay (e.g. Agentforce scaling)
  broll    -- full-bleed Pexels cuts every ~10-15s in the gaps

plus rotating, never-reused backdrops. Karaoke captions are untouched (they
stay in SegmentCard's SubtitleBand above every beat layer).
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Callable

import broll_queries as bq

CHAPTER_MS = 5000
# 2026-09-28 items 1+5: a StatHero appears STAT_LEAD_MS before the number is
# spoken so a count-up can finish (or the static value snap in) at/before the
# spoken word, then stays fully readable for >= STAT_MIN_HOLD_MS after landing.
STAT_LEAD_MS = 1400
STAT_MAX_EARLY_MS = 600          # placement may pull a hero this much earlier
STAT_FADE_OUT_MS = 400
STAT_MIN_HOLD_MS = 4500          # readable time after the value lands
STAT_LONG_HOLD_MS = 5200         # money / "up to" / long labels
STAT_MS = STAT_LEAD_MS + STAT_MAX_EARLY_MS + STAT_MIN_HOLD_MS + STAT_FADE_OUT_MS   # 6900
STAT_WITH_COMPANIONS_MS = STAT_MS + 1500
QUADRANT_MS = 12000
QUADRANT_HIGHLIGHT_MS = 8000
CHART_MS = 13000
QUOTE_MS = 7000
CARD_PAD_MS = 700
MIN_BROLL_MS = 5000
TARGET_BROLL_FRAC = 0.45     # of each segment's runtime
BACKDROP_EVERY_MS = 55_000

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_RANGE_RE = re.compile(r"\d\s*(?:[-–—]|to)\s*\d")
# 2026-09-28 item 6: limits / approximations are never counted up (static snap).
_QUALIFIER_WORDS = (
    "up to", "about", "around", "nearly", "over", "more than", "less than", "fewer than",
    "almost", "roughly", "approximately", "approx", "at least", "at most", "as much as",
    "as many as", "as high as", "as low as", "under", "above", "below", "close to", "upwards of",
)
_QUALIFIER_RE = re.compile(
    r"(?:^|\b)(?:" + "|".join(re.escape(w) for w in _QUALIFIER_WORDS) + r")\b|[~≈<>≤≥]|\+\s*$|\d\+",
    re.I,
)


def stat_is_static(value: str) -> bool:
    """True when a stat must render as a static snap/fade of the full string:
    ranges ("5-10%", "3 to 5x"), limits / approximations ("Up to 6.9%",
    "over $4 billion", "~40%", "10,000+"), or multiple numbers."""
    v = (value or "").strip()
    if not v:
        return True
    if _RANGE_RE.search(v) or _QUALIFIER_RE.search(v):
        return True
    return len(_NUM_RE.findall(v)) != 1


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _norm_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2]


def _stems(text: str) -> set[str]:
    stop = {"the", "and", "for", "with", "from", "into", "via", "without", "increase",
            "improvement", "increased"}
    return {w[:4] for w in _norm_words(text) if w not in stop}


def num_tokens(value: str) -> list[str]:
    return [t.replace(",", "") for t in _NUM_RE.findall(value or "")]


def stat_signature(value: str) -> str:
    toks = num_tokens(value)
    if not toks:
        return (value or "").strip().lower()
    return "-".join(toks) + ("%" if "%" in (value or "") else "")


def _text_has_numbers(text: str, toks: list[str]) -> bool:
    blob = (text or "").lower().replace(",", "")
    for t in toks:
        if not re.search(r"(?<![\d.])" + re.escape(t) + r"(?![\d])", blob):
            return False
    return True


def _truncate(text: str, n: int) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) <= n:
        return t
    cut = t[: n - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:—-") + "…"


def _line_windows(seg: dict[str, Any], durations: dict[str, int], seg_start_abs: int) -> list[dict[str, Any]]:
    out = []
    cur = seg_start_abs
    for i, line in enumerate(seg.get("lines") or []):
        d = int(durations.get(line.get("line_id"), 0))
        out.append({
            "idx": i,
            "line_id": line.get("line_id"),
            "host": line.get("host_id") or "A",
            "text": (line.get("text") or "").strip(),
            "start": cur,
            "end": cur + d,
        })
        cur += d + int(line.get("pause_after_ms") or 0)
    return out


def _number_spoken_ms(value: str, ln: dict[str, Any], seg_words: list[dict[str, Any]]) -> int | None:
    """Absolute ms when the stat's leading number is actually spoken in its
    line (karaoke word timing), else a character-proportional estimate."""
    toks = num_tokens(value)
    if not toks:
        return None
    lead = re.sub(r"\D", "", toks[0])
    if not lead:
        return None
    for w in seg_words:
        if int(w.get("line_idx", -1)) != int(ln["idx"]):
            continue
        if lead and lead == re.sub(r"\D", "", str(w.get("word") or ""))[: len(lead)] and re.search(r"\d", str(w.get("word") or "")):
            return int(w.get("start_ms", 0))
    text = ln.get("text") or ""
    m = re.search(re.escape(toks[0]).replace(",", ",?"), text) or re.search(lead, text.replace(",", ""))
    if m and text:
        frac = m.start() / max(1, len(text))
        return int(ln["start"] + frac * (ln["end"] - ln["start"]))
    return None


def _line_idx_for_id(lines: list[dict[str, Any]], line_id: str | None) -> int | None:
    if not line_id:
        return None
    for ln in lines:
        if ln["line_id"] == line_id:
            return ln["idx"]
    return None


def _ov_line_idx(ov: dict[str, Any], key: str, lines: list[dict[str, Any]], default: int) -> int:
    """Overlay anchors are 1-based ints per the prompt; accept line_id strings too."""
    raw = ov.get(key)
    if raw is None:
        return default
    try:
        return int(raw) - 1
    except (TypeError, ValueError):
        idx = _line_idx_for_id(lines, str(raw))
        return default if idx is None else idx


def _dominant_color(lines: list[dict[str, Any]], i0: int, i1: int, ca: str, cb: str) -> str:
    a = b = 0
    for ln in lines[max(0, i0): max(i0 + 1, i1 + 1)]:
        if ln["host"] == "B":
            b += 1
        else:
            a += 1
    return ca if a >= b else cb


# ---------------------------------------------------------------------------
# stats (items 1 + 7)
# ---------------------------------------------------------------------------

_COMPANION_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?%?)\s+([A-Za-z][A-Za-z-]{2,})")


def _companions(subtext: str, main_value: str, seg_text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    main = set(num_tokens(main_value))
    for m in _COMPANION_RE.finditer(subtext or ""):
        val, word = m.group(1), m.group(2)
        toks = num_tokens(val)
        if not toks or set(toks) & main:
            continue
        if not _text_has_numbers(seg_text, toks):
            continue   # never show digits the hosts didn't say
        out.append({"value": val, "label": word.lower()})
        if len(out) >= 3:
            break
    return out


_STAT_SCALE_WORDS = ("trillion", "billion", "million", "thousand")
_STAT_BARE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([A-Za-z%]*)")


_SPOKEN_QUALIFIER_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(_QUALIFIER_WORDS, key=len, reverse=True)) + r")\s*$",
    re.I,
)


def enrich_stat_value(value: str, spoken: str) -> str:
    """Restore currency / scale words the planner dropped from a bare StatCallout
    number, using the line that speaks it ("4" + "over $4 billion" -> "Over $4 billion";
    "304million" -> "$304 million"). A limit / approximation spoken right before
    the number ("up to 90%") is kept too, so the hero renders static (item 6).
    Non-bare values are returned unchanged."""
    v = (value or "").strip()
    m = _STAT_BARE_RE.fullmatch(v)
    if not m:
        return value
    num, unit = m.group(1), m.group(2)
    sm = re.search(
        r"(?P<cur>[$\u20ac\u00a3])?\s*(?<![\d.])" + re.escape(num)
        + r"(?!\.?\d|,\d)\s*(?P<scale>trillion|billion|million|thousand|percent|%)?",
        spoken or "", re.I)
    cur = (sm.group("cur") if sm else None) or ""
    scale = ((sm.group("scale") if sm else None) or "").lower()
    qual = ""
    if sm:
        qm = _SPOKEN_QUALIFIER_RE.search((spoken or "")[: sm.start()])
        if qm:
            qual = qm.group(1).lower()
            qual = qual[0].upper() + qual[1:] + " "
    ul = unit.lower()
    if ul == "percent":
        unit, ul = "%", "%"
    if not unit and scale:
        unit = ul = "%" if scale in ("percent", "%") else scale
    if ul == "%":
        return f"{qual}{cur}{num}%"
    if ul in _STAT_SCALE_WORDS:
        return f"{qual}{cur}{num} {ul}"
    return f"{qual}{cur}{num}{unit}"


def collect_stats(
    episode: dict[str, Any],
    seg_lines: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Script stats (overlays type=stat + StatCallout/StatAnimation/PercentageRing
    visuals), grounded in dialogue, deduplicated episode-wide (item 7)."""
    raw: list[dict[str, Any]] = []
    for si, seg in enumerate(episode.get("segments") or []):
        lines = seg_lines[si]
        for ov in seg.get("overlays") or []:
            if isinstance(ov, dict) and ov.get("type") == "stat" and ov.get("value"):
                show = _ov_line_idx(ov, "show_at_line", lines, 0)
                raw.append({"seg_idx": si, "label": str(ov.get("label") or ""),
                            "value": str(ov.get("value")), "subtext": str(ov.get("subtext") or ""),
                            "hint_idx": show, "source": "overlay"})
        for v in seg.get("visuals") or []:
            if not isinstance(v, dict):
                continue
            tpl = v.get("template")
            d = v.get("data") or {}
            if tpl in ("StatCallout", "StatAnimation"):
                val = d.get("value")
                if val is None:
                    continue
                value = f"{val}{d.get('unit') or ''}".strip()
                label = str(d.get("label") or d.get("caption") or "")
                sub = str(d.get("subtext") or d.get("source") or "")
            elif tpl == "PercentageRing":
                if d.get("percentage") is None:
                    continue
                value = f"{d.get('percentage')}%"
                label = str(d.get("label") or "")
                sub = ""
            else:
                continue
            anchor = v.get("start_with_line") or v.get("start_after_line")
            idx = _line_idx_for_id(lines, anchor)
            raw.append({"seg_idx": si, "label": label, "value": value, "subtext": sub,
                        "hint_idx": idx if idx is not None else 0, "source": f"visual:{tpl}"})

    kept: list[dict[str, Any]] = []
    for st in raw:
        si = st["seg_idx"]
        lines = seg_lines[si]
        seg_text = " ".join(ln["text"] for ln in lines)
        toks = num_tokens(st["value"])
        if toks and not _text_has_numbers(seg_text, toks):
            continue   # ungrounded -> drop (on-screen digits must match speech)
        sig = stat_signature(st["value"])
        dup = None
        for k in kept:
            if k["signature"] != sig:
                continue
            a = _stems(k["label"] + " " + k["subtext"])
            b = _stems(st["label"] + " " + st["subtext"])
            if not a or not b or (a & b):
                dup = k
                break
        if dup is not None:
            dup["duplicates"].append({"seg_idx": si, "label": st["label"], "value": st["value"], "source": st["source"]})
            if not dup["subtext"] and st["subtext"]:
                dup["subtext"] = st["subtext"]
            continue
        # Spoken line: first line (from hint-2 onward, else anywhere) that says every number.
        spoken_idx = None
        if toks:
            order = list(range(max(0, st["hint_idx"] - 2), len(lines))) + list(range(0, max(0, st["hint_idx"] - 2)))
            for i in order:
                if _text_has_numbers(lines[i]["text"], toks):
                    spoken_idx = i
                    break
        idx = spoken_idx if spoken_idx is not None else max(0, min(len(lines) - 1, st["hint_idx"]))
        if spoken_idx is not None:
            st = {**st, "value": enrich_stat_value(st["value"], lines[spoken_idx]["text"])}
        kept.append({
            **st,
            "signature": sig,
            "line_idx": idx,
            "is_range": stat_is_static(st["value"]),
            "companions": _companions(st["subtext"], st["value"], seg_text),
            "duplicates": [],
        })
    return kept


# ---------------------------------------------------------------------------
# chapters (item 4)
# ---------------------------------------------------------------------------

def _too_similar(a: str, b: str) -> bool:
    sa, sb = set(_norm_words(a)), set(_norm_words(b))
    if not sa or not sb:
        return True
    return len(sa & sb) / max(1, min(len(sa), len(sb))) >= 0.6


def _chapter_hook(seg: dict[str, Any], lines: list[dict[str, Any]], title: str, stats: list[dict[str, Any]]) -> str:
    seg_stats = [s for s in stats if s["seg_idx"] == seg.get("_idx")]
    if seg_stats:
        s = seg_stats[0]
        lab = s["label"] or s["subtext"]
        if lab:
            return _truncate(f"{s['value']} — {lab}", 70)
    bullets = [ov for ov in (seg.get("overlays") or []) if isinstance(ov, dict) and ov.get("type") == "bullets"]
    for ov in bullets:
        if ov.get("title") and not _too_similar(str(ov["title"]), title):
            return _truncate(str(ov["title"]), 70)
    for ov in bullets:
        items = [_split_label(str(x))[0] for x in (ov.get("items") or []) if str(x).strip()]
        if len(items) >= 3 and all(len(x) <= 26 for x in items[:3]):
            return " · ".join(items[:3])
        if items and 12 <= len(items[0]) <= 70:
            return items[0]
    for ln in lines[:4]:
        for sent in re.split(r"(?<=[.!?])\s+", ln["text"]):
            if sent.endswith("?") and 25 <= len(sent) <= 90:
                return _truncate(sent, 80)
    first = re.split(r"(?<=[.!?])\s+", lines[0]["text"])[0] if lines else ""
    return _truncate(first, 70)


def pick_chapters(
    episode: dict[str, Any],
    seg_lines: list[list[dict[str, Any]]],
    seg_durations: list[int],
    total_ms: int,
    stats: list[dict[str, Any]],
    clean_title: Callable[[str], str],
) -> dict[int, dict[str, Any]]:
    segs = episode.get("segments") or []
    n = len(segs)
    if n < 3:
        return {}
    planned: dict[int, dict[str, Any]] = {}
    for si, seg in enumerate(segs):
        for v in seg.get("visuals") or []:
            if isinstance(v, dict) and v.get("template") in ("TitleCard", "ChapterCard"):
                d = v.get("data") or {}
                t = d.get("title") or d.get("chapter_title")
                if t:
                    planned.setdefault(si, {"title": str(t), "subtitle": str(d.get("subtitle") or ""), "source": v.get("template")})
    # Chapters live mid-episode: skip the cold-open segment and the close.
    mid = set(range(1, n - 1))
    chosen: dict[int, dict[str, Any]] = {si: p for si, p in planned.items() if si in mid}
    target = max(len(planned), int(round(total_ms / 60000 / 5)))
    target = min(target, len(mid))
    if len(chosen) < target:
        rest = sorted((si for si in mid if si not in chosen), key=lambda si: seg_durations[si], reverse=True)
        for si in rest[: target - len(chosen)]:
            chosen[si] = {"title": "", "subtitle": "", "source": "auto"}
    out: dict[int, dict[str, Any]] = {}
    for num, si in enumerate(sorted(chosen), start=2):
        seg = dict(segs[si])
        seg["_idx"] = si
        base_title = clean_title(str(seg.get("title") or f"Part {num}"))
        ch = chosen[si]
        title = ch["title"] or base_title
        sub = ch["subtitle"] or _chapter_hook(seg, seg_lines[si], title, stats)
        out[si] = {"title": _truncate(title, 60), "subtitle": _truncate(sub, 90),
                   "chapter_number": si + 1, "source": ch["source"]}
    return out


# ---------------------------------------------------------------------------
# diagrams / cards (items 5 + 2)
# ---------------------------------------------------------------------------

_HUB_RE = re.compile(r"collaborat|partner|roles?\b|responsibilit|operating model|team model|who does|ownership", re.I)
_FLOW_RE = re.compile(r"steps?\b|process|how to|roadmap|stages?\b|phases?\b|journey|path\b|workflow|readiness|playbook|checklist|sequence|lifecycle|rollout|scaling", re.I)
_PILLAR_RE = re.compile(r"framework|pillars?|takeaways?|principles|strateg|keys?\b|elements|foundations?|levers|components|leading|examples|companies|benefits|advantages", re.I)
_GOAL_RE = re.compile(r"goal|shared|outcome|result|north star", re.I)
_TAKEAWAY_RE = re.compile(r"takeaways?|pillars?|framework|principles", re.I)
KEY_TAKEAWAYS_RE = re.compile(r"\b(key\s+)?take-?aways?\b|\bkey lessons\b", re.I)
_IMPERATIVE = {
    "make", "structure", "prioritize", "map", "reframe", "prompt", "designate", "build", "foster",
    "shift", "define", "pilot", "measure", "scale", "launch", "curate", "identify", "assess", "align",
    "create", "train", "monitor", "move", "focus", "cultivate", "target", "start", "set", "run", "test",
    "iterate", "deploy", "prepare", "establish", "invest", "review", "evaluate", "clean", "collect",
    "audit", "document", "select", "choose", "hire", "stop", "begin", "route", "expand",
}


def _imperative_share(items: list[str]) -> float:
    if not items:
        return 0.0
    hits = sum(1 for i in items if (re.findall(r"[a-z]+", i.lower()) or [""])[0] in _IMPERATIVE)
    return hits / len(items)


def _split_label(item: str) -> tuple[str, str]:
    m = re.match(r"^\s*([^:]{2,40}):\s*(.+)$", item)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return item.strip(), ""


def bullets_to_diagram(title: str, items: list[str], used_kinds: set[str]) -> dict[str, Any] | None:
    items = [str(x).strip() for x in items if str(x).strip()]
    if not (2 <= len(items) <= 6):
        return None
    pairs = [_split_label(i) for i in items]
    labeled = sum(1 for _l, d in pairs if d)
    kind = None
    if KEY_TAKEAWAYS_RE.search(title or ""):
        # Locked default (2026-09-28 item 9): the closing "Key Takeaways" card
        # is always the multi-box pillars card, even if pillars was used earlier.
        nodes = [{"label": _truncate(lab, 44 if det else 64), "detail": _truncate(det, 70)} for lab, det in pairs]
        return {"kind": "pillars", "title": title, "nodes": nodes[:6], "center": ""}
    if _HUB_RE.search(title or ""):
        kind = "hub"
    elif _FLOW_RE.search(title or ""):
        kind = "flow"
    elif _TAKEAWAY_RE.search(title or ""):
        kind = "pillars"
    elif _imperative_share(items) >= 0.75 and len(items) <= 5:
        kind = "flow"
    elif labeled >= max(2, math.ceil(0.6 * len(items))) or _PILLAR_RE.search(title or ""):
        kind = "pillars"
    if kind is None:
        return None
    if kind in used_kinds:
        kind = "pillars" if kind != "pillars" else ("flow" if len(items) <= 5 and "flow" not in used_kinds else None)
        if kind is None:
            return None
    center = ""
    nodes = []
    for lab, det in pairs:
        if kind == "hub" and not center and _GOAL_RE.search(lab):
            center = det or lab
            continue
        nodes.append({"label": _truncate(lab, 44 if det else 64), "detail": _truncate(det, 70)})
    if kind == "hub":
        center = center or re.sub(r"^(new|key)\s+", "", title or "", flags=re.I)
        if len(nodes) < 2:
            return None
    return {"kind": kind, "title": title, "nodes": nodes[:6], "center": _truncate(center, 48)}


def _visual_to_card(v: dict[str, Any]) -> dict[str, Any] | None:
    tpl = v.get("template")
    d = v.get("data") or {}
    if tpl == "FlowDiagram":
        steps = d.get("steps") or []
        nodes = [{"label": _truncate(str(s.get("label") or ""), 44), "detail": _truncate(str(s.get("description") or ""), 70)}
                 for s in steps if isinstance(s, dict) and s.get("label")]
        if len(nodes) >= 2:
            return {"type": "diagram", "kind": "flow", "title": str(d.get("title") or ""), "nodes": nodes[:6]}
    if tpl == "ConceptComparison":
        left, right = d.get("left") or {}, d.get("right") or {}
        if left and right:
            return {"type": "diagram", "kind": "comparison", "title": str(d.get("title") or ""),
                    "columns": [
                        {"label": str(left.get("label") or ""), "points": [str(p) for p in (left.get("points") or [])][:4]},
                        {"label": str(right.get("label") or ""), "points": [str(p) for p in (right.get("points") or [])][:4]},
                    ]}
    if tpl == "TimelineVisual":
        ev = d.get("events") or []
        nodes = [{"label": str(e.get("year") or ""), "detail": _truncate(str(e.get("label") or ""), 60)} for e in ev if isinstance(e, dict)]
        if len(nodes) >= 2:
            return {"type": "diagram", "kind": "timeline", "title": str(d.get("title") or ""), "nodes": nodes[:6]}
    if tpl == "GrowthCurve":
        pts = d.get("points") or []
        dps = []
        for p in pts:
            try:
                dps.append({"label": str(p.get("x")), "value": float(p.get("y"))})
            except (TypeError, ValueError, AttributeError):
                continue
        if len(dps) >= 2:
            return {"type": "chart", "title": str(d.get("label") or ""), "chart_type": "growth", "data_points": dps}
    if tpl == "KeyTermDefinition" and d.get("term"):
        return {"type": "diagram", "kind": "definition", "title": str(d.get("term")),
                "nodes": [{"label": str(d.get("category") or "Key term"), "detail": _truncate(str(d.get("definition") or ""), 160)}]}
    if tpl == "PullQuote" and d.get("quote"):
        return {"type": "diagram", "kind": "quote", "title": str(d.get("speaker") or ""),
                "nodes": [{"label": _truncate(str(d.get("quote")), 180), "detail": ""}]}
    return None


def _stat_needs_long_hold(card: dict[str, Any]) -> bool:
    v = str(card.get("value") or "")
    return bool(re.search(r"[$€£]|million|billion|trillion", v, re.I)) or stat_is_static(v) \
        or len(str(card.get("label") or "")) > 34


def stat_hold_ms(beat: dict[str, Any]) -> int:
    """Fully-readable time after the value lands (count done / static snapped)."""
    show, hide = int(beat["show_ms"]), int(beat["hide_ms"])
    land = int(beat.get("land_ms", show))
    landed = max(show + 450, land)   # fade-in finished and value final
    return (hide - STAT_FADE_OUT_MS) - landed


def _card_duration(card: dict[str, Any]) -> int:
    t = card["type"]
    if t == "chapter":
        return CHAPTER_MS
    if t == "stat":
        base = STAT_WITH_COMPANIONS_MS if card.get("companions") else STAT_MS
        return base + (STAT_LONG_HOLD_MS - STAT_MIN_HOLD_MS if _stat_needs_long_hold(card) else 0)
    if t == "chart":
        return CHART_MS
    if t == "diagram":
        if card.get("kind") == "quadrant":
            return QUADRANT_HIGHLIGHT_MS if (card.get("quadrant") or {}).get("highlight") is not None else QUADRANT_MS
        if card.get("kind") in ("quote", "definition"):
            return QUOTE_MS
        n = len(card.get("nodes") or card.get("columns") or [])
        return int(_clamp(4000 + 2200 * n, 9000, 15000))
    if t == "bullets":
        n = len(card.get("items") or [])
        return int(_clamp(3000 + 2000 * n, 8000, 14000))
    return 8000


# ---------------------------------------------------------------------------
# 2x2 strategy quadrant (2026-09-28 item 2)
# ---------------------------------------------------------------------------
#
# Framework articles often cross two dimensions into four strategies / types
# (Bouquet et al.: value-chain control x technological breadth). The planner
# emits a QuadrantOverlay (DiagramOverlay kind="quadrant") when the SCRIPT
# grounds all of it -- nothing is invented:
#   1. a script `quadrant` overlay (the prompt asks Gemini for one), or
#   2. a heuristic: two named axes (a "two dimensions / axes" bullets card or
#      quoted names) + four named strategies whose position is stated as
#      high/low on BOTH axes (bullets like "Low value-chain control" or a line
#      like "...applies to companies with low X and low Y").
# Only a complete, consistent 2x2 (four distinct cells) is used.

_AXES_TITLE_RE = re.compile(r"\b(two|2)\b.{0,40}\b(dimensions?|axes|axis|factors|forces|drivers|variables|lenses)\b", re.I)
_HIGH_WORDS = ("high", "higher", "strong", "stronger", "extensive", "broad", "broader", "wide", "deep",
               "greater", "significant", "tight", "full", "heavy", "considerable", "great", "much")
_LOW_WORDS = ("low", "lower", "limited", "narrow", "narrower", "weak", "weaker", "little", "minimal",
              "modest", "less", "light", "small", "restricted")
_LEVEL_RE = re.compile(r"\b(" + "|".join(_HIGH_WORDS + _LOW_WORDS) + r")\b", re.I)
_STRATEGY_PREFIX_RE = re.compile(r"^\s*(?:strategy|type|quadrant|option|approach|play)\s*(?:\d+|[ivx]+)?\s*[:.\-–—]\s*", re.I)


def _axis_norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower().replace("-", " ")).strip()


def _axis_clean(item: str) -> str:
    t = re.sub(r"^\s*(high|low)\s*[:\-–—]\s*", "", str(item or ""), flags=re.I)
    t = _split_label(t)[0] if ":" in t else t
    return re.sub(r"\s+", " ", t).strip(" .\"'‘’“”")


def _axis_pattern(axis: str) -> re.Pattern[str]:
    words = [w for w in _axis_norm(axis).split() if w]
    return re.compile(r"\b" + r"[\s\-]+".join(re.escape(w) for w in words) + r"\b", re.I)


def _level_before(text: str, pos: int) -> str | None:
    """high/low for the level word within ~4 words before an axis mention."""
    window = text[max(0, pos - 48): pos]
    hits = list(_LEVEL_RE.finditer(window))
    if not hits:
        return None
    tail = window[hits[-1].end():]
    if len(re.findall(r"[A-Za-z]+", tail)) > 2:
        return None
    return "high" if hits[-1].group(1).lower() in _HIGH_WORDS else "low"


def _levels_in(text: str, axes: list[str]) -> tuple[str | None, str | None]:
    out: list[str | None] = []
    for ax in axes:
        lvl = None
        for m in _axis_pattern(ax).finditer(text or ""):
            lvl = _level_before(text, m.start())
            if lvl:
                break
        out.append(lvl)
    return out[0], out[1]


def _strategy_name(raw: str) -> str:
    t = _STRATEGY_PREFIX_RE.sub("", str(raw or "")).strip()
    t = re.sub(r"\s+(strategy|strategies|approach|quadrant)$", "", t, flags=re.I)
    return t.strip(" .\"'‘’“”:")


def _find_axes(episode: dict[str, Any]) -> tuple[list[str], int] | None:
    segs = episode.get("segments") or []
    for si, seg in enumerate(segs):
        for ov in seg.get("overlays") or []:
            if not isinstance(ov, dict) or ov.get("type") != "bullets":
                continue
            items = [str(x) for x in (ov.get("items") or []) if str(x).strip()]
            if len(items) == 2 and _AXES_TITLE_RE.search(str(ov.get("title") or "")):
                axes = [_axis_clean(i) for i in items]
                if all(2 <= len(a) <= 40 for a in axes):
                    return axes, si
    # Fallback: "two ... dimensions" line + the first two quoted terms nearby.
    for si, seg in enumerate(segs):
        text = " ".join(str(ln.get("text") or "") for ln in seg.get("lines") or [])
        if not _AXES_TITLE_RE.search(text):
            continue
        quoted = []
        for m in re.finditer(r"['\"‘“]([A-Za-z][A-Za-z\- ]{3,38})['\"’”]", text):
            q = m.group(1).strip()
            if 1 <= len(q.split()) <= 4 and _axis_norm(q) not in {_axis_norm(x) for x in quoted}:
                quoted.append(q)
        if len(quoted) >= 2:
            return quoted[:2], si
    return None


def _script_quadrant(seg: dict[str, Any]) -> dict[str, Any] | None:
    for ov in seg.get("overlays") or []:
        if isinstance(ov, dict) and ov.get("type") == "quadrant":
            q = normalize_quadrant(ov)
            if q:
                return {**q, "_ov": ov}
    return None


def normalize_quadrant(ov: dict[str, Any]) -> dict[str, Any] | None:
    """Validate a script `quadrant` overlay -> {x_axis, y_axis, cells[4]}."""
    xa, ya = ov.get("x_axis") or {}, ov.get("y_axis") or {}
    if isinstance(xa, str):
        xa = {"label": xa}
    if isinstance(ya, str):
        ya = {"label": ya}
    if not (xa.get("label") and ya.get("label")):
        return None
    cells = []
    seen = set()
    for c in ov.get("cells") or []:
        if not isinstance(c, dict) or not c.get("label"):
            continue
        x = 1 if str(c.get("x", "")).lower() in ("high", "1", "right") else 0
        y = 1 if str(c.get("y", "")).lower() in ("high", "1", "top") else 0
        if (x, y) in seen:
            return None
        seen.add((x, y))
        cells.append({"label": _truncate(str(c["label"]), 34), "detail": _truncate(str(c.get("detail") or ""), 60),
                      "x": x, "y": y})
    if len(cells) != 4:
        return None
    return {
        "x_axis": {"label": _truncate(str(xa["label"]), 40), "low": str(xa.get("low") or "Low"), "high": str(xa.get("high") or "High")},
        "y_axis": {"label": _truncate(str(ya["label"]), 40), "low": str(ya.get("low") or "Low"), "high": str(ya.get("high") or "High")},
        "cells": cells,
    }


def detect_quadrant(episode: dict[str, Any], seg_lines: list[list[dict[str, Any]]]) -> dict[str, Any] | None:
    """Return a grounded 2x2 plan or None.

    {"x_axis", "y_axis", "cells": [{label, detail, x, y}], "source",
     "intro": (seg_idx, line_idx), "positions": {(si, ov_idx|None): (cell_idx, line_idx)},
     "presence": {(si, ov_idx): {cell_idx: detail}}}
    """
    segs = episode.get("segments") or []
    for si, seg in enumerate(segs):
        q = _script_quadrant(seg)
        if q:
            ov = q.pop("_ov")
            lines = seg_lines[si]
            idx = max(0, min(len(lines) - 1, _ov_line_idx(ov, "show_at_line", lines, 0))) if lines else 0
            q.update({"source": "script", "intro": (si, idx), "positions": {}, "presence": {},
                      "title": str(ov.get("title") or ""), "consumed": {(si, (seg.get("overlays") or []).index(ov))}})
            return q
    found = _find_axes(episode)
    if not found:
        return None
    axes, axis_seg = found
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    positions: dict[tuple[int, Any], tuple[tuple[int, int], int]] = {}
    for si, seg in enumerate(segs):
        lines = seg_lines[si]
        # (a) bullets card listing the levels ("Low value-chain control", "High technological breadth").
        for oi, ov in enumerate(seg.get("overlays") or []):
            if not isinstance(ov, dict) or ov.get("type") != "bullets":
                continue
            items = [str(x) for x in (ov.get("items") or []) if str(x).strip()]
            lx, ly = _levels_in(" ; ".join(items), axes)
            if not (lx and ly):
                continue
            name = _strategy_name(str(ov.get("title") or seg.get("title") or ""))
            if not name or _axis_pattern(axes[0]).search(name) or _axis_pattern(axes[1]).search(name):
                name = _strategy_name(str(seg.get("title") or ""))
            if not name:
                continue
            extras = [i for i in items if not (_axis_pattern(axes[0]).search(i) or _axis_pattern(axes[1]).search(i))]
            key = (1 if lx == "high" else 0, 1 if ly == "high" else 0)
            if key in cells and _axis_norm(cells[key]["label"]) != _axis_norm(name):
                return None   # contradictory -> don't guess
            cells.setdefault(key, {"label": _truncate(name, 34), "detail": _truncate(extras[0], 60) if extras else ""})
            i0 = max(0, min(len(lines) - 1, _ov_line_idx(ov, "show_at_line", lines, 0))) if lines else 0
            positions[(si, oi)] = (key, i0)
        # (b) a spoken line stating both levels; name from quotes / segment title.
        for ln in lines:
            lx, ly = _levels_in(ln["text"], axes)
            if not (lx and ly):
                continue
            key = (1 if lx == "high" else 0, 1 if ly == "high" else 0)
            qn = re.search(r"[\"“‘']([A-Z][A-Za-z\- ]{3,34})[.,]?[\"”’']", ln["text"])
            name = _strategy_name(qn.group(1)) if qn else _strategy_name(str(seg.get("title") or ""))
            if not name or name.lower().startswith(("segment", "part")):
                continue
            if key not in cells:
                cells[key] = {"label": _truncate(name, 34), "detail": ""}
            if not any(k[0] == si for k in positions):
                positions[(si, None)] = (key, ln["idx"])
            break
    if len(cells) != 4:
        return None
    order = [(0, 0), (1, 0), (0, 1), (1, 1)]
    cell_list = [{**cells[k], "x": k[0], "y": k[1]} for k in order]
    idx_of = {k: i for i, k in enumerate(order)}
    # Intro: last line of the axes segment that names both axes (else its middle).
    lines = seg_lines[axis_seg]
    both = [ln["idx"] for ln in lines if _axis_pattern(axes[0]).search(ln["text"]) and _axis_pattern(axes[1]).search(ln["text"])]
    intro_line = both[-1] if both else (len(lines) // 2 if lines else 0)
    # "Presence" cards: bullets whose items name >= 3 of the 4 strategies.
    presence: dict[tuple[int, int], dict[int, str]] = {}
    for si, seg in enumerate(segs):
        for oi, ov in enumerate(seg.get("overlays") or []):
            if not isinstance(ov, dict) or ov.get("type") != "bullets" or (si, oi) in positions:
                continue
            det: dict[int, str] = {}
            for it in ov.get("items") or []:
                for k, c in cells.items():
                    if _axis_pattern(c["label"]).search(str(it)):
                        rest = re.sub(_axis_pattern(c["label"]).pattern, "", str(it), flags=re.I)
                        det[idx_of[k]] = _truncate(rest.strip(" ()-–—:,"), 40)
            if len(det) >= 3:
                presence[(si, oi)] = det
    return {
        "x_axis": {"label": _truncate(axes[0], 40), "low": "Low", "high": "High"},
        "y_axis": {"label": _truncate(axes[1], 40), "low": "Low", "high": "High"},
        "cells": cell_list,
        "source": "heuristic",
        "intro": (axis_seg, intro_line),
        "positions": {k: (idx_of[v[0]], v[1]) for k, v in positions.items()},
        "presence": presence,
        "title": "",
        "consumed": set(),
    }


def quadrant_card(q: dict[str, Any], *, title: str = "", highlight: int | None = None,
                  details: dict[int, str] | None = None) -> dict[str, Any]:
    cells = [dict(c) for c in q["cells"]]
    if details:
        for i, d in details.items():
            if d:
                cells[i]["detail"] = d
    ttl = title or q.get("title") or f"{q['x_axis']['label']} × {q['y_axis']['label']}"
    return {"type": "diagram", "kind": "quadrant", "title": _truncate(ttl, 70),
            "quadrant": {"x_axis": q["x_axis"], "y_axis": q["y_axis"], "cells": cells, "highlight": highlight}}


# ---------------------------------------------------------------------------
# scheduling
# ---------------------------------------------------------------------------

def _overlaps(a0: int, a1: int, busy: list[tuple[int, int]], pad: int) -> bool:
    for b0, b1 in busy:
        if a0 < b1 + pad and a1 + pad > b0:
            return True
    return False


def _place(card: dict[str, Any], lo: int, hi: int, busy: list[tuple[int, int]]) -> tuple[int, int] | None:
    dur = card["dur"]
    desired = int(_clamp(card["desired"], lo, max(lo, hi - dur)))
    max_shift = card.get("max_shift", 20000)
    early = card.get("max_early", 1500)
    step = 250
    for d in range(0, max_shift + 1, step):
        for t in (desired + d, desired - d) if d and d <= early else (desired + d,):
            if t < lo or t + dur > hi:
                continue
            if not _overlaps(t, t + dur, busy, CARD_PAD_MS):
                return t, t + dur
    if card["type"] in ("diagram", "bullets", "chart") and dur > 7000:
        card = {**card, "dur": 7000}
        return _place(card, lo, hi, busy)
    return None


def _gaps(lo: int, hi: int, busy: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out = []
    cur = lo
    for b0, b1 in sorted(busy):
        if b0 > cur:
            out.append((cur, b0))
        cur = max(cur, b1)
    if hi > cur:
        out.append((cur, hi))
    return out


def plan_episode(
    *,
    episode: dict[str, Any],
    durations: dict[str, int],
    seg_timings: list[dict[str, Any]],
    card_windows: list[tuple[int, int]],
    word_timings: list[dict[str, Any]],
    color_a: str,
    color_b: str,
    total_ms: int,
    picker: bq.PexelsPicker | None,
    clean_title: Callable[[str], str],
    complete_items: Callable[[str, list[str]], list[str]],
    gemini_query: Callable[[str, str], list[str]] | None = None,
    log: Callable[[str], None] = lambda m: print(m, file=sys.stderr),
) -> dict[str, Any]:
    segs = episode.get("segments") or []
    topic = str(episode.get("topic") or "")
    seg_lines = [_line_windows(seg, durations, int(t["start_ms"])) for seg, t in zip(segs, seg_timings)]
    seg_durs = [int(t["duration_ms"]) for t in seg_timings]
    stats = collect_stats(episode, seg_lines)
    chapters = pick_chapters(episode, seg_lines, seg_durs, total_ms, stats, clean_title)
    try:
        quad = detect_quadrant(episode, seg_lines)
    except Exception as exc:  # never fatal
        log(f"[plan] quadrant detection failed: {exc!r}")
        quad = None

    words_by_seg: dict[int, list[dict[str, Any]]] = {}
    for w in word_timings:
        words_by_seg.setdefault(int(w.get("segment_idx", -1)), []).append(w)

    recent_queries: list[str] = []
    case_uses: dict[str, int] = {}
    per_segment: list[dict[str, Any]] = []
    report = {"segments": [], "stats": [], "chapters": [], "broll": [], "backdrops": [], "dropped": [],
              "quadrant": None}
    if quad:
        report["quadrant"] = {"source": quad["source"], "x_axis": quad["x_axis"]["label"],
                              "y_axis": quad["y_axis"]["label"],
                              "cells": [{"label": c["label"], "x": c["x"], "y": c["y"]} for c in quad["cells"]],
                              "intro_segment": quad["intro"][0] + 1}
        log(f"[plan] 2x2 quadrant: {quad['x_axis']['label']} x {quad['y_axis']['label']} -> "
            + ", ".join(c["label"] for c in quad["cells"]) + f" ({quad['source']})")
    salt = 0

    for si, seg in enumerate(segs):
        cs, ce = card_windows[si]
        lines = seg_lines[si]
        seg_title = clean_title(str(seg.get("title") or f"Segment {si + 1}"))
        seg_text = " ".join(ln["text"] for ln in lines)
        cards: list[dict[str, Any]] = []
        used_kinds: set[str] = set()

        if si in chapters:
            ch = chapters[si]
            cards.append({"type": "chapter", **ch, "desired": cs + 150, "prio": 0, "max_shift": 4000,
                          "max_early": 0, "accent": color_a})
        for st in (s for s in stats if s["seg_idx"] == si):
            ln = lines[st["line_idx"]] if lines else {"start": cs, "end": cs, "idx": 0, "text": ""}
            spoken_ms = _number_spoken_ms(st["value"], ln, words_by_seg.get(si, [])) if lines else None
            # Items 1+6: the value must be final at/before the spoken number.
            # Count-ups start STAT_LEAD_MS early; static values just before.
            land_abs = spoken_ms if spoken_ms is not None else ln["start"] + 400
            lead = 700 if st["is_range"] else STAT_LEAD_MS
            desired = max(cs, land_abs - lead)
            cards.append({"type": "stat", "label": st["label"], "value": st["value"],
                          "subtext": st["subtext"], "companions": st["companions"],
                          "is_range": st["is_range"], "static": st["is_range"], "land_abs": land_abs,
                          "desired": desired, "prio": 1,
                          "max_shift": 16000, "max_early": STAT_MAX_EARLY_MS,
                          "speakerColor": _dominant_color(lines, ln["idx"], ln["idx"], color_a, color_b)})
        # Item 2: 2x2 quadrant -- full matrix where the axes are explained.
        if quad and quad["intro"][0] == si and lines:
            il = max(0, min(len(lines) - 1, quad["intro"][1]))
            cards.append({**quadrant_card(quad), "desired": lines[il]["start"], "prio": 2, "max_shift": 30000,
                          "max_early": 6000, "speakerColor": _dominant_color(lines, il, il, color_a, color_b)})
        for oi, ov in enumerate(seg.get("overlays") or []):
            if not isinstance(ov, dict) or ov.get("type") not in ("bullets", "chart"):
                continue
            if not lines:
                continue
            if quad and (si, oi) in quad.get("consumed", set()):
                continue
            # Item 2: a strategy's position card -> quadrant with its cell lit;
            # a card naming >= 3 strategies -> full quadrant with those details.
            if quad and ((si, oi) in quad["positions"] or (si, oi) in quad["presence"]):
                i0 = max(0, min(len(lines) - 1, _ov_line_idx(ov, "show_at_line", lines, 0)))
                color = _dominant_color(lines, i0, i0 + 2, color_a, color_b)
                if (si, oi) in quad["positions"]:
                    cell, _li = quad["positions"][(si, oi)]
                    qc = quadrant_card(quad, title=quad["cells"][cell]["label"], highlight=cell)
                else:
                    qc = quadrant_card(quad, title=str(ov.get("title") or ""), details=quad["presence"][(si, oi)])
                cards.append({**qc, "desired": lines[i0]["start"], "prio": 2, "speakerColor": color})
                continue
            i0 = max(0, min(len(lines) - 1, _ov_line_idx(ov, "show_at_line", lines, 0)))
            i1 = max(i0, min(len(lines) - 1, _ov_line_idx(ov, "hide_at_line", lines, i0 + 2)))
            color = _dominant_color(lines, i0, i1, color_a, color_b)
            desired = lines[i0]["start"] if lines else cs
            if ov["type"] == "chart":
                dps = []
                for dp in ov.get("data_points") or []:
                    try:
                        dps.append({"label": str(dp.get("label") or ""), "value": float(dp.get("value") or 0)})
                    except (TypeError, ValueError, AttributeError):
                        continue
                if len(dps) >= 2:
                    cards.append({"type": "chart", "title": str(ov.get("title") or ""), "chart_type": str(ov.get("chart_type") or "growth"),
                                  "data_points": dps, "desired": desired, "prio": 2, "speakerColor": color})
                continue
            title = str(ov.get("title") or "")
            items = complete_items(title, [str(x) for x in (ov.get("items") or []) if x])
            dia = bullets_to_diagram(title, items, used_kinds)
            if dia:
                used_kinds.add(dia["kind"])
                cards.append({"type": "diagram", **dia, "desired": desired, "prio": 2, "speakerColor": color})
            else:
                cards.append({"type": "bullets", "title": title, "items": items, "desired": desired,
                              "prio": 3, "speakerColor": color})
        if quad and (si, None) in quad["positions"] and lines and not any(k[0] == si and k[1] is not None for k in quad["positions"]):
            cell, li = quad["positions"][(si, None)]
            cards.append({**quadrant_card(quad, title=quad["cells"][cell]["label"], highlight=cell),
                          "desired": lines[li]["start"], "prio": 2,
                          "speakerColor": _dominant_color(lines, li, li, color_a, color_b)})
        for v in seg.get("visuals") or []:
            if not isinstance(v, dict):
                continue
            card = _visual_to_card(v)
            if not card:
                continue
            anchor = v.get("start_with_line") or v.get("start_after_line")
            idx = _line_idx_for_id(lines, anchor)
            idx = idx if idx is not None else 0
            if v.get("start_after_line") and not v.get("start_with_line"):
                idx = min(len(lines) - 1, idx + 1)
            card.update({"desired": lines[idx]["start"] if lines else cs, "prio": 2,
                         "speakerColor": _dominant_color(lines, idx, idx, color_a, color_b)})
            cards.append(card)

        # --- place cards by priority (no overlaps => nothing masks a stat) ---
        busy: list[tuple[int, int]] = []
        placed: list[dict[str, Any]] = []
        for card in sorted(cards, key=lambda c: (c["prio"], c["desired"])):
            card["dur"] = _card_duration(card)
            slot = _place(card, cs, ce - 400, busy)
            if not slot:
                report["dropped"].append({"segment": si + 1, "type": card["type"], "title": card.get("title") or card.get("value")})
                continue
            if card["type"] == "stat":
                # Item 5: guarantee the readable hold after landing (e.g. when the
                # segment end pulled the hero earlier) by extending into free time.
                probe = {"show_ms": slot[0], "hide_ms": slot[1], "land_ms": card.get("land_abs", slot[0])}
                short = STAT_MIN_HOLD_MS - stat_hold_ms(probe)
                if short > 0:
                    nxt = min([b0 for b0, _b1 in busy if b0 >= slot[1]] + [ce - 400]) - CARD_PAD_MS
                    slot = (slot[0], max(slot[1], min(slot[1] + short, nxt)))
            busy.append(slot)
            placed.append({**card, "start": slot[0], "end": slot[1]})

        # --- B-roll cuts in the gaps (item 2) ---
        seg_words = words_by_seg.get(si, [])
        gaps = _gaps(cs, ce - 300, busy)
        gap_total = sum(b - a for a, b in gaps) or 1
        frac = _clamp(TARGET_BROLL_FRAC * (ce - cs) / gap_total, 0.35, 0.78)
        broll_windows: list[tuple[int, int]] = []
        prev_case: str | None = None
        k = 0
        for g0, g1 in gaps:
            t = g0
            if g1 - g0 < MIN_BROLL_MS + 1000:
                continue
            # Open segment on the backdrop for a beat (visual reset), else cut right away.
            if g0 == cs and si not in chapters:
                t += 3000
            while g1 - t >= MIN_BROLL_MS:
                L = 10000 + (k % 3) * 1500
                end = min(g1, t + L)
                if g1 - end < 3000 and (g1 - t) <= 16000:
                    end = g1
                broll_windows.append((t, end))
                k += 1
                R = int(_clamp(L * (1 - frac) / frac, 3000, 12000))
                t = end + R
        for (b0, b1) in broll_windows:
            wtext = " ".join(str(w.get("word") or "") for w in seg_words if b0 - 1500 <= int(w.get("start_ms", 0)) < b1)
            if not wtext.strip():
                wtext = seg_text
            ctx = f"{seg_title}. {seg_text}"
            # Item 4: a named company / case in (or just before) this window
            # drives the query (Zillow -> homes, Pfizer/BioNTech -> vaccine lab).
            case_name, named = bq.named_case_queries(wtext)
            if not named:
                # Case named just before the cut: carry it over once, never for
                # a run of windows (avoids a wall of the same domain shot).
                near = " ".join(str(w.get("word") or "") for w in seg_words if b0 - 15000 <= int(w.get("start_ms", 0)) < b0 - 1500)
                case_name, named = bq.named_case_queries("", near)
                if case_name and case_name == prev_case:
                    case_name, named = None, []
            prev_case = case_name
            if case_name and named:
                n_used = case_uses.get(case_name, 0)
                case_uses[case_name] = n_used + 1
                r = n_used % len(named)
                named = named[r:] + named[:r]   # vary the shot across repeats
            queries: list[str] = list(named)
            gem: list[str] = []
            if gemini_query is not None:
                try:
                    gem = gemini_query(wtext, f"{topic} | {seg_title}")
                except Exception as exc:  # never fatal
                    log(f"[plan] gemini query failed: {exc!r}")
                    gem = []
                for q in gem:
                    q2 = bq.clean_query(q)
                    ok, why = bq.validate_query(q2)
                    if ok and q2 not in queries and not (bq.is_llm_context(wtext) and re.search(r"robot|android|machine arm", q2)):
                        queries.append(q2)
            local = bq.local_query_candidates(wtext, ctx, salt=salt, recent=recent_queries[-4:])
            salt += 1
            queries += [q for q in local if q not in queries]
            photo = picker.pick(queries, context_text=f"{wtext} {ctx}", role="broll") if picker else None
            entry = {"start": b0, "end": b1, "queries": queries[:4], "gemini": gem[:3], "named_case": case_name,
                     "photo_url": (photo or {}).get("url") or "", "photo_id": (photo or {}).get("id"),
                     "query": (photo or {}).get("query") or (queries[0] if queries else ""),
                     "excerpt": _truncate(wtext, 140)}
            if entry["query"]:
                recent_queries.append(entry["query"])
            report["broll"].append({"segment": si + 1, **entry})
            if entry["photo_url"]:
                placed.append({"type": "broll", "start": b0, "end": b1, "photo_url": entry["photo_url"],
                               "query": entry["query"], "photo_id": entry["photo_id"]})

        # --- rotating backdrops, never reused (item 8) ---
        n_bd = int(_clamp(math.ceil((ce - cs) / BACKDROP_EVERY_MS), 1, 5))
        backdrops = []
        span = (ce - cs) / n_bd
        for b in range(n_bd):
            b0 = int(cs + b * span)
            b1 = int(cs + (b + 1) * span) if b < n_bd - 1 else ce
            btext = " ".join(ln["text"] for ln in lines if ln["end"] > b0 and ln["start"] < b1) or seg_text
            qs = bq.local_query_candidates(btext, f"{seg_title}. {seg_text}", salt=si * 7 + b + 3, limit=6)
            bd_case, bd_named = bq.named_case_queries(btext, "")
            if bd_named:
                qs = [q for q in bd_named if q not in qs][:2] + qs
            photo = picker.pick(qs, context_text=f"{btext} {seg_text}", role="backdrop") if picker else None
            if photo:
                backdrops.append({"start": b0, "end": b1, "photo_url": photo["url"], "query": photo["query"], "photo_id": photo["id"]})
                report["backdrops"].append({"segment": si + 1, "query": photo["query"], "photo_id": photo["id"]})

        # --- rebase to card-relative ms ---
        beats = []
        for p in sorted(placed, key=lambda c: c["start"]):
            b = {k2: v2 for k2, v2 in p.items() if k2 not in ("start", "end", "desired", "prio", "dur", "max_shift", "max_early", "land_abs")}
            b["show_ms"] = int(p["start"] - cs)
            b["hide_ms"] = int(p["end"] - cs)
            if b["type"] == "stat":
                # Card-relative ms of the spoken number: StatHero finishes any
                # count-up by then (snaps if the card started too late).
                b["land_ms"] = int(p.get("land_abs", p["start"]) - cs)
                b["count_up"] = (not b.get("static")) and (b["land_ms"] - b["show_ms"]) >= 570
                report.setdefault("stat_holds", []).append({
                    "segment": si + 1, "value": b.get("value"), "static": bool(b.get("static")),
                    "count_up": b["count_up"], "lead_ms": b["land_ms"] - b["show_ms"],
                    "hold_after_land_ms": stat_hold_ms(b)})
            beats.append(b)
        bds = [{"start_ms": int(b["start"] - cs), "end_ms": int(b["end"] - cs), "photo_url": b["photo_url"],
                "query": b["query"], "photo_id": b["photo_id"]} for b in backdrops]
        broll_ms = sum(b["hide_ms"] - b["show_ms"] for b in beats if b["type"] == "broll")
        card_ms = sum(b["hide_ms"] - b["show_ms"] for b in beats if b["type"] != "broll")
        longest_static = 0
        cur = 0
        for b in beats:
            longest_static = max(longest_static, b["show_ms"] - cur)
            cur = max(cur, b["hide_ms"])
        longest_static = max(longest_static, (ce - cs) - cur)
        per_segment.append({"beats": beats, "backdrops": bds})
        report["segments"].append({
            "segment": si + 1, "title": seg_title, "duration_s": round((ce - cs) / 1000, 1),
            "beats": {t: sum(1 for b in beats if b["type"] == t) for t in ("chapter", "stat", "diagram", "bullets", "chart", "broll")},
            "broll_pct": round(100 * broll_ms / max(1, ce - cs), 1),
            "cards_pct": round(100 * card_ms / max(1, ce - cs), 1),
            "longest_without_change_s": round(longest_static / 1000, 1),
            "backdrops": len(bds),
        })

    report["stats"] = [{"segment": s["seg_idx"] + 1, "value": s["value"], "label": s["label"],
                        "range_static": s["is_range"], "companions": s["companions"],
                        "deduped": s["duplicates"]} for s in stats]
    report["chapters"] = [{"segment": si + 1, **c} for si, c in sorted(chapters.items())]
    tot = sum(max(1, ce - cs) for cs, ce in card_windows) or 1
    broll_total = sum(b["hide_ms"] - b["show_ms"] for s in per_segment for b in s["beats"] if b["type"] == "broll")
    report["summary"] = {
        "broll_pct_of_segments": round(100 * broll_total / tot, 1),
        "chapters": len(chapters),
        "stat_heroes": sum(1 for s in per_segment for b in s["beats"] if b["type"] == "stat"),
        "diagrams": sum(1 for s in per_segment for b in s["beats"] if b["type"] == "diagram"),
        "charts": sum(1 for s in per_segment for b in s["beats"] if b["type"] == "chart"),
        "bullets": sum(1 for s in per_segment for b in s["beats"] if b["type"] == "bullets"),
        "broll_cuts": sum(1 for s in per_segment for b in s["beats"] if b["type"] == "broll"),
        "unique_photos": len(picker.used) if picker else 0,
        "pexels_api_calls": picker.api_calls if picker else 0,
        "rejected_photos": (picker.rejects[:40] if picker else []),
        "quadrants": sum(1 for s in per_segment for b in s["beats"] if b.get("kind") == "quadrant"),
        "named_case_broll": sum(1 for b in report["broll"] if b.get("named_case")),
        "stat_min_hold_ms": min([h["hold_after_land_ms"] for h in report.get("stat_holds", [])] or [0]),
    }
    return {"segments": per_segment, "report": report}


__all__ = ["plan_episode", "collect_stats", "pick_chapters", "bullets_to_diagram", "stat_signature",
           "stat_is_static", "enrich_stat_value", "detect_quadrant", "normalize_quadrant", "stat_hold_ms"]
