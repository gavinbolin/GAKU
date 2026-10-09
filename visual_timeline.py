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
from collections import Counter
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
# First time a 2x2 is introduced it has to stay up long enough to read all four cells.
QUADRANT_INTRO_MIN_MS = 10000
# Karaoke-only stretches (no card, overlay, or B-roll cut) cannot run longer than this.
MAX_GRAPHICS_GAP_S = 18
MAX_GRAPHICS_GAP_MS = MAX_GRAPHICS_GAP_S * 1000
# A StatHero must still be on screen this long after the stat's last spoken word.
STAT_END_PAD_MS = 450
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


_STAT_TAIL_RE = re.compile(
    r"^(?:%|percent|per|cent|million|billion|trillion|thousand|dollars?|usd|times)$",
    re.I,
)


def _stat_spoken_end_ms(value: str, ln: dict[str, Any], seg_words: list[dict[str, Any]]) -> int | None:
    """Absolute ms when the stat's last spoken word ends (number, then percent/scale)."""
    start = _number_spoken_ms(value, ln, seg_words)
    if start is None:
        return None
    words = sorted(
        (w for w in seg_words if int(w.get("line_idx", -1)) == int(ln["idx"])),
        key=lambda w: int(w.get("start_ms", 0)),
    )
    if not words:
        return int(ln.get("end") or start)
    idx = None
    for i, w in enumerate(words):
        if int(w.get("start_ms", 0)) >= start - 40 and re.search(r"\d", str(w.get("word") or "")):
            idx = i
            break
    if idx is None:
        return int(ln.get("end") or start)
    end = int(words[idx].get("end_ms", start))
    for w in words[idx + 1: idx + 4]:
        bare = re.sub(r"[^\w%]", "", str(w.get("word") or ""))
        if not bare or not _STAT_TAIL_RE.match(bare):
            break
        end = int(w.get("end_ms", end))
    return end


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


# "from 16% to 99%" / "16% → 99%" is a change, not a range. Snap to the final
# value so a count-up can never show 61% while the line already says 99%.
# "5 to 10%" has no unit on the first number and no arrow, so it stays a range.
_TRANSITION_RE = re.compile(
    r"(?P<a>\d[\d,]*(?:\.\d+)?)\s*(?P<ua>percent|%|×|x)?"
    r"\s*(?:→|->|to)\s*"
    r"(?P<b>\d[\d,]*(?:\.\d+)?)\s*(?P<ub>percent|%|×|x)?",
    re.I,
)


def _transition_parts(text: str) -> tuple[str, str, str] | None:
    """(final display, start digits, end digits) for a from-X-to-Y change."""
    t = (text or "").strip()
    if not t:
        return None
    m = _TRANSITION_RE.search(t)
    if not m:
        return None
    ua = (m.group("ua") or "").lower()
    ub = (m.group("ub") or "").lower()
    spanned = t[m.start(): m.end()]
    if not ua and not re.search(r"→|->", spanned):
        return None
    unit = ub or ua
    if unit in ("percent", "%"):
        unit = "%"
    elif unit in ("x", "×"):
        unit = "×"
    else:
        unit = ""
    return f"{m.group('b')}{unit}", m.group("a").replace(",", ""), m.group("b").replace(",", "")


def transition_final_value(text: str) -> str | None:
    """Final display value for a from-X-to-Y change, or None when `text` is not one."""
    parts = _transition_parts(text)
    return parts[0] if parts else None


def apply_multiplier_symbol(value: str, spoken: str = "") -> str:
    """Render a multiplier with the × sign ('3x' / '3 times' → '3×')."""
    v = (value or "").strip()
    m = re.fullmatch(
        r"(?P<pre>.*?)(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?:x|×|times)\b(?P<post>.*)",
        v,
        re.I,
    )
    if m:
        post = (m.group("post") or "").strip()
        if post == "" or re.fullmatch(r"[A-Za-z]+", post):
            pre = (m.group("pre") or "").strip()
            body = f"{m.group('num')}×" + (f" {post}" if post else "")
            return f"{pre} {body}".strip() if pre else body
    if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", v) and re.search(
        r"(?<![\d.])" + re.escape(v) + r"(?![\d])\s*(?:x|×|times)\b", spoken or "", re.I,
    ):
        return f"{v}×"
    return v


def is_bare_unitless(value: str) -> bool:
    """Small number with no unit, currency, or scale ('3', 'Over 3'). '10,000' is not bare."""
    v = (value or "").strip()
    v = re.sub(
        r"^(?:" + "|".join(re.escape(w) for w in sorted(_QUALIFIER_WORDS, key=len, reverse=True)) + r")\s+",
        "",
        v,
        count=1,
        flags=re.I,
    ).strip()
    return bool(re.fullmatch(r"\d{1,3}(?:\.\d+)?", v))


def normalize_stat_display(value: str, spoken: str = "") -> tuple[str | None, bool]:
    """(display string, force_static). None drops the callout.

    The string is what Remotion draws and what the overlay JSON stores
    ('More than 40%', '99%', '3×') — not a shortened core.
    A from-X%-to-Y% change snaps to the final value and never counts up.
    """
    raw = (value or "").strip()
    spoken = spoken or ""
    own = _transition_parts(raw)
    if own:
        return own[0], True
    heard = _transition_parts(spoken)
    if heard:
        # Snap only when this callout's number is an endpoint of that change,
        # so a nearby "3 times" on the same line is not rewritten to 99%.
        raw_toks = {t.replace(",", "") for t in num_tokens(raw)}
        if raw_toks & {heard[1], heard[2]}:
            return heard[0], True
    shown = apply_multiplier_symbol(enrich_stat_value(raw, spoken), spoken)
    if is_bare_unitless(shown):
        return None, True
    return shown, stat_is_static(shown)


def stat_values_collide(a: str, b: str) -> bool:
    sa, sb = stat_signature(a), stat_signature(b)
    return bool(sa) and sa == sb


def dedupe_adjacent_stat_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One stat hero per value when two would land in the same beat or the next one.

    A non-stat card between them (diagram, chapter, bullets) breaks adjacency,
    so the same figure can return later in the episode.
    """
    order = sorted(
        range(len(cards)),
        key=lambda i: (int(cards[i].get("desired") or 0), int(cards[i].get("prio") or 9), i),
    )
    drop: set[int] = set()
    prev_stat: int | None = None
    for i in order:
        card = cards[i]
        if card.get("type") != "stat":
            prev_stat = None
            continue
        if prev_stat is not None and stat_values_collide(
            str(cards[prev_stat].get("value") or ""), str(card.get("value") or ""),
        ):
            drop.add(i)
            continue
        prev_stat = i
    if not drop:
        return cards
    return [c for i, c in enumerate(cards) if i not in drop]


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
        # Spoken line: first line (from hint-2 onward, else anywhere) that says every number.
        spoken_idx = None
        if toks:
            order = list(range(max(0, st["hint_idx"] - 2), len(lines))) + list(range(0, max(0, st["hint_idx"] - 2)))
            for i in order:
                if _text_has_numbers(lines[i]["text"], toks):
                    spoken_idx = i
                    break
        idx = spoken_idx if spoken_idx is not None else max(0, min(len(lines) - 1, st["hint_idx"]))
        spoken = lines[spoken_idx]["text"] if spoken_idx is not None and lines else ""
        display, force_static = normalize_stat_display(st["value"], spoken)
        if not display:
            continue   # bare number with no unit (a lone "3") — don't hero it
        disp_toks = num_tokens(display)
        if disp_toks and not _text_has_numbers(seg_text, disp_toks):
            continue
        st = {**st, "value": display}
        sig = stat_signature(display)
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
        kept.append({
            **st,
            "signature": sig,
            "line_idx": idx,
            "is_range": force_static or stat_is_static(display),
            "companions": _companions(st["subtext"], display, seg_text),
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


def _subtitle_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def _content_subtitle_candidates(
    seg: dict[str, Any], lines: list[dict[str, Any]], title: str, stats: list[dict[str, Any]],
) -> list[str]:
    out: list[str] = []
    hook = _chapter_hook(seg, lines, title, stats)
    if hook:
        out.append(hook)
    for ov in seg.get("overlays") or []:
        if not isinstance(ov, dict):
            continue
        if ov.get("title"):
            out.append(str(ov["title"]))
        if ov.get("type") == "bullets":
            for it in ov.get("items") or []:
                lab = _split_label(str(it))[0]
                if lab:
                    out.append(lab)
    for ln in lines:
        out.extend(_sentences(ln.get("text") or ""))
    return out


def _shared_with_siblings(sub: str, sibling_text: str) -> bool:
    """True when `sub` (even if truncated) is the same sentence another chapter says."""
    key = _subtitle_key((sub or "").replace("…", ""))
    if not key:
        return True
    sib_key = _subtitle_key(sibling_text)
    if key in sib_key:
        return True
    for sent in _sentences(sibling_text or ""):
        sk = _subtitle_key(sent)
        if not sk:
            continue
        if sk.startswith(key) or key.startswith(sk):
            return True
    return False


def unique_chapter_subtitle(
    preferred: str,
    seg: dict[str, Any],
    lines: list[dict[str, Any]],
    title: str,
    stats: list[dict[str, Any]],
    *,
    blocked: set[str],
    sibling_text: str,
) -> str:
    """Subtitle that is not in `blocked`. Collisions prefer a sentence that
    belongs to this chapter and not to the other chapters."""
    ranked: list[tuple[int, str]] = []
    for raw in [preferred] + _content_subtitle_candidates(seg, lines, title, stats):
        sub = _truncate(re.sub(r"\s+", " ", (raw or "")).strip(), 90)
        key = _subtitle_key(sub.replace("…", ""))
        if not sub or not key or key in blocked:
            continue
        own = not _shared_with_siblings(sub, sibling_text)
        ranked.append((0 if own else 1, sub))
    if ranked:
        ranked.sort(key=lambda t: t[0])
        return ranked[0][1]
    for w in _norm_words(" ".join(ln.get("text") or "" for ln in lines)):
        if w in set(_norm_words(title)):
            continue
        if _subtitle_key(w) not in blocked and w not in siblings:
            return w
    n = 2
    base = _truncate(preferred or title or "Chapter", 70)
    while _subtitle_key(f"{base} {n}") in blocked:
        n += 1
    return f"{base} {n}"


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
    # Chapters live mid-episode: skip the cold open, the close, and any
    # sign-off segment so its chip is not the previous Key Takeaways chapter.
    mid = {si for si in range(1, n - 1) if not is_close_segment(segs[si], si, n)}
    chosen: dict[int, dict[str, Any]] = {si: p for si, p in planned.items() if si in mid}
    target = max(len(planned), int(round(total_ms / 60000 / 5)))
    target = min(target, len(mid))
    if len(chosen) < target:
        rest = sorted((si for si in mid if si not in chosen), key=lambda si: seg_durations[si], reverse=True)
        for si in rest[: target - len(chosen)]:
            chosen[si] = {"title": "", "subtitle": "", "source": "auto"}
    drafts: list[tuple[int, dict[str, Any], str, str, str]] = []
    for num, si in enumerate(sorted(chosen), start=2):
        seg = dict(segs[si])
        seg["_idx"] = si
        base_title = clean_title(str(seg.get("title") or f"Part {num}"))
        ch = chosen[si]
        title = ch["title"] or base_title
        sub = ch["subtitle"] or _chapter_hook(seg, seg_lines[si], title, stats)
        drafts.append((si, seg, title, sub, ch["source"]))
    counts = Counter(_subtitle_key(sub) for _si, _seg, _title, sub, _src in drafts)
    collided = {k for k, n in counts.items() if k and n > 1}
    used: set[str] = set()
    out: dict[int, dict[str, Any]] = {}
    for si, seg, title, sub, source in drafts:
        key = _subtitle_key(sub)
        sibling = " ".join(
            ln.get("text") or ""
            for sj, lines in enumerate(seg_lines)
            if sj != si
            for ln in lines
        )
        if not key or key in collided or key in used:
            sub = unique_chapter_subtitle(
                sub, seg, seg_lines[si], title, stats,
                blocked=collided | used, sibling_text=sibling,
            )
        else:
            sub = _truncate(re.sub(r"\s+", " ", sub).strip(), 90)
        used.add(_subtitle_key(sub))
        out[si] = {"title": _truncate(title, 60), "subtitle": sub,
                   "chapter_number": si + 1, "source": source}
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
        # Full text — Pillars wraps and shrinks the font. Never store an ellipsis.
        nodes = [{"label": lab, "detail": det} for lab, det in pairs]
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
            if card.get("intro"):
                return max(QUADRANT_MS, QUADRANT_INTRO_MIN_MS)
            if (card.get("quadrant") or {}).get("highlight") is not None:
                return QUADRANT_HIGHLIGHT_MS
            return QUADRANT_MS
        if card.get("kind") in ("quote", "definition"):
            return QUOTE_MS
        n = len(card.get("nodes") or card.get("columns") or [])
        dur = int(_clamp(4000 + 2200 * n, 9000, 15000))
        # Key Takeaways stay up long enough to read every pillar (at least ~8s).
        if KEY_TAKEAWAYS_RE.search(str(card.get("title") or "")):
            return max(8000, dur)
        return dur
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


def _highlight_index(q: dict[str, Any], raw: Any) -> int | None:
    """Script `highlight` may be a cell label or an index. None = full matrix."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int) or (isinstance(raw, str) and str(raw).strip().isdigit()):
        i = int(raw)
        return i if 0 <= i < len(q.get("cells") or []) else None
    needle = str(raw).strip().lower()
    for i, c in enumerate(q.get("cells") or []):
        label = str(c.get("label") or "").strip().lower()
        if label and (label == needle or needle in label or label in needle):
            return i
    return None


def script_quadrant_seats(
    episode: dict[str, Any], seg_lines: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Every valid script `quadrant` overlay, in episode order.

    detect_quadrant() returns on the first overlay, which is why a six-overlay
    episode only ever drew one 2x2 (and the reviewer saw B-roll at the others).
    """
    seats: list[dict[str, Any]] = []
    for si, seg in enumerate(episode.get("segments") or []):
        lines = seg_lines[si] if si < len(seg_lines) else []
        for oi, ov in enumerate(seg.get("overlays") or []):
            if not isinstance(ov, dict) or ov.get("type") != "quadrant":
                continue
            q = normalize_quadrant(ov)
            if not q:
                continue
            idx = max(0, min(len(lines) - 1, _ov_line_idx(ov, "show_at_line", lines, 0))) if lines else 0
            hl = _highlight_index(q, ov.get("highlight"))
            seats.append({
                **q,
                "seg_idx": si,
                "ov_idx": oi,
                "line_idx": idx,
                "highlight": hl,
                "title": str(ov.get("title") or ""),
                "source": "script",
            })
    intro_i = 0
    for i, sq in enumerate(seats):
        if sq["highlight"] is None:
            intro_i = i
            break
    for i, sq in enumerate(seats):
        sq["intro"] = i == intro_i
    return seats


def is_close_segment(seg: dict[str, Any], si: int, n_segs: int) -> bool:
    """The sign-off segment (sNN_close), not the Key Takeaways recap before it."""
    sid = str(seg.get("segment_id") or "")
    kind = str(seg.get("kind") or "")
    blob = f"{sid} {kind} {seg.get('title') or ''}"
    if kind.lower() == "close" or re.search(r"(^|_)close$", sid, re.I) or re.search(r"\bclose_cta\b", sid, re.I):
        return True
    if si == n_segs - 1 and re.search(r"\b(close|sign-?off)\b", blob, re.I) and not _RECAP_SEG_RE.search(blob):
        return True
    return False


def _cta_line(text: str) -> str:
    m = re.search(r"(stop piloting and start leading[.!]?)", text or "", re.I)
    if m:
        s = m.group(1).strip()
        return s[0].upper() + s[1:]
    sentence = _sentences(text or "")
    raw = sentence[-1] if sentence else (text or "")
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) <= 90:
        return raw
    return raw[:87].rsplit(" ", 1)[0]


def close_cta_card(seg: dict[str, Any], lines: list[dict[str, Any]], ov: dict[str, Any] | None = None) -> dict[str, Any]:
    """A short landing line plus a light CTA. Never titled Key Takeaways."""
    title = re.sub(r"\s+", " ", str(seg.get("title") or "Close")).strip()
    if KEY_TAKEAWAYS_RE.search(title):
        title = "Stop Piloting, Start Leading"
    items = [str(x).strip() for x in ((ov or {}).get("items") or []) if str(x).strip()]
    landing = items[0] if items else "Find where you actually sit"
    spoken = _cta_line(lines[-1]["text"] if lines else "Stop piloting and start leading.")
    card_items = [landing]
    if spoken and spoken.lower() not in landing.lower():
        card_items.append(spoken)
    elif len(items) > 1:
        card_items.append(items[-1])
    return {
        "type": "bullets",
        "role": "close_cta",
        "title": _truncate(title, 70) if len(title) > 70 else title,
        "items": card_items[:3],
        "min_dur": 8000,
        "hold_to_end": True,
    }


def _force_into_window(
    card: dict[str, Any], cs: int, ce: int, placed: list[dict[str, Any]], color: str,
) -> dict[str, Any] | None:
    """Seat a card that placement dropped, clearing non-stat overlaps."""
    dur = int(card.get("min_dur") or card.get("dur") or _card_duration(card))
    dur = min(dur, max(5000, ce - cs - 400))
    anchor = int(card.get("desired") or cs)
    end = min(ce - 300, max(anchor, cs) + dur)
    start = max(cs + 100, end - dur)
    if end - start < 5000:
        return None
    kept = []
    for p in placed:
        if p.get("type") in ("stat", "chapter"):
            if p["start"] < end and start < p["end"]:
                start = p["end"] + CARD_PAD_MS
                end = min(ce - 300, start + dur)
            kept.append(p)
            continue
        if p["end"] <= start or p["start"] >= end:
            kept.append(p)
    if end - start < 5000:
        return None
    placed[:] = kept
    seated = {**card, "start": start, "end": end, "speakerColor": card.get("speakerColor") or color}
    placed.append(seated)
    return seated


def longest_graphics_gap_ms(beats: list[dict[str, Any]], duration_ms: int) -> int:
    """Longest stretch with no card and no B-roll cut, inside one segment."""
    cur = 0
    longest = 0
    for b in sorted(beats, key=lambda x: int(x.get("show_ms") or 0)):
        if b.get("type") not in ("chapter", "stat", "diagram", "bullets", "chart", "broll"):
            continue
        show = int(b.get("show_ms") or 0)
        hide = int(b.get("hide_ms") or show)
        longest = max(longest, show - cur)
        cur = max(cur, hide)
    return max(longest, int(duration_ms) - cur)


def _gap_fill_card(lines: list[dict[str, Any]], start: int, end: int, color: str) -> dict[str, Any]:
    ln = None
    for candidate in lines:
        if candidate["start"] < end and candidate["end"] > start:
            ln = candidate
            break
    if ln is None and lines:
        ln = lines[min(len(lines) - 1, len(lines) // 2)]
    text = _sentences((ln or {}).get("text") or "")
    sentence = text[0] if text else "The point of this stretch"
    sentence = re.sub(r"\s+", " ", sentence).strip()
    if len(sentence) > 110:
        sentence = sentence[:107].rsplit(" ", 1)[0]
    title = " ".join(sentence.split()[:6]) or "On this point"
    return {
        "type": "bullets",
        "role": "gap_fill",
        "title": title,
        "items": [sentence],
        "start": start,
        "end": end,
        "speakerColor": color,
    }


def _window_words(seg_words: list[dict[str, Any]], start: int, end: int) -> str:
    return " ".join(
        str(w.get("word") or "")
        for w in seg_words
        if start - 400 <= int(w.get("start_ms", 0)) < end
    )


def fill_graphics_gaps(
    placed: list[dict[str, Any]],
    cs: int,
    ce: int,
    lines: list[dict[str, Any]],
    color: str,
    *,
    seg_words: list[dict[str, Any]] | None = None,
    seg_text: str = "",
    picker: Any = None,
) -> int:
    """Insert a card or a B-roll cut anywhere karaoke would run longer than MAX_GRAPHICS_GAP_MS.

    Returns how many fills were added. A photo-less B-roll beat would not
    change the picture, so when the picker has no still the fill is a card.
    """
    added = 0
    guard = 0
    while guard < 80:
        guard += 1
        spans = sorted((int(p["start"]), int(p["end"])) for p in placed)
        cur = cs
        hole: tuple[int, int] | None = None
        for a, b in spans:
            if a - cur > MAX_GRAPHICS_GAP_MS:
                hole = (cur, a)
                break
            cur = max(cur, b)
        if hole is None and ce - cur > MAX_GRAPHICS_GAP_MS:
            hole = (cur, ce)
        if hole is None:
            break
        g0, g1 = hole
        dur = 7000
        start = g0 + MAX_GRAPHICS_GAP_MS - dur
        if start < g0 + 150:
            start = g0 + 150
        end = min(g1 - 100, start + dur)
        if end - start < 4000:
            start = g0 + 150
            end = min(g1 - 100, start + dur)
        if end - start < 3000:
            break
        photo = None
        wtext = _window_words(seg_words or [], start, end) or seg_text
        if picker is not None and wtext.strip():
            _case, named = bq.named_case_queries(wtext)
            qs = list(named) + bq.local_query_candidates(wtext, wtext, limit=4)
            photo = picker.pick(qs, context_text=wtext, role="broll")
        if photo and photo.get("url"):
            placed.append({
                "type": "broll",
                "start": int(start),
                "end": int(end),
                "photo_url": photo["url"],
                "query": photo.get("query") or "",
                "photo_id": photo.get("id"),
                "role": "gap_fill",
            })
        else:
            placed.append(_gap_fill_card(lines, int(start), int(end), color))
        added += 1
    return added


def sync_stat_inventory(
    episode: dict[str, Any],
    stats: list[dict[str, Any]],
    per_segment: list[dict[str, Any]],
) -> dict[str, Any]:
    """Planned heroes that rendered are present in the overlay inventory.

    A StatCallout visual (17000, $304M when it only lived on a visual) is not
    "missing" once a StatHero beat exists. range_static values are written
    back in full ("More than 30%", not "30%").
    """
    rendered: list[dict[str, Any]] = []
    rendered_sigs: set[str] = set()
    for si, seg_plan in enumerate(per_segment):
        for b in seg_plan.get("beats") or []:
            if b.get("type") != "stat":
                continue
            sig = stat_signature(str(b.get("value") or ""))
            rendered_sigs.add(sig)
            rendered.append({
                "segment": si + 1,
                "value": b.get("value"),
                "label": b.get("label") or "",
                "signature": sig,
                "show_ms": b.get("show_ms"),
                "hide_ms": b.get("hide_ms"),
                "spoken_end_ms": b.get("spoken_end_ms"),
            })
    missing: list[dict[str, Any]] = []
    for st in stats:
        sig = st["signature"]
        if sig in rendered_sigs:
            continue
        missing.append({
            "segment": st["seg_idx"] + 1,
            "value": st["value"],
            "label": st["label"],
            "source": st["source"],
        })
    for st in stats:
        if st["signature"] not in rendered_sigs:
            continue
        seg = (episode.get("segments") or [])[st["seg_idx"]]
        overlays = seg.setdefault("overlays", [])
        matched = False
        for ov in overlays:
            if not isinstance(ov, dict) or ov.get("type") != "stat":
                continue
            if stat_signature(str(ov.get("value") or "")) != st["signature"]:
                continue
            ov["value"] = st["value"]
            if st.get("label") and not ov.get("label"):
                ov["label"] = st["label"]
            matched = True
        if matched:
            continue
        lines = seg.get("lines") or []
        show = int(st.get("line_idx") or 0) + 1
        overlays.append({
            "type": "stat",
            "label": st.get("label") or "",
            "value": st["value"],
            "subtext": st.get("subtext") or "",
            "show_at_line": show,
            "hide_at_line": min(len(lines) or show, show + 2),
            "source": "rendered_hero",
        })
    return {"rendered": rendered, "missing": missing}


def _seal_segment_holds(
    placed: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    cs: int,
    ce: int,
    seg: dict[str, Any],
    si: int,
    n_segs: int,
    color: str,
) -> None:
    """Force a dropped 2x2 or close CTA, then keep each StatHero through speech.

    Quadrants are pushed rather than deleted. A close CTA holds to the end of
    the card window so the sign-off is not left on Key Takeaways chrome.
    """
    def _quad_key(card: dict[str, Any]) -> tuple[Any, Any]:
        return (card.get("title"), (card.get("quadrant") or {}).get("highlight"))

    seated = {_quad_key(p) for p in placed if p.get("kind") == "quadrant"}
    for card in cards:
        if card.get("kind") != "quadrant" or _quad_key(card) in seated:
            continue
        if _force_into_window(card, cs, ce, placed, color):
            seated.add(_quad_key(card))
    if is_close_segment(seg, si, n_segs) and not any(p.get("role") == "close_cta" for p in placed):
        for card in cards:
            if card.get("role") == "close_cta":
                _force_into_window(card, cs, ce, placed, color)
                break
    for p in list(placed):
        if p.get("role") != "close_cta" or not p.get("hold_to_end"):
            continue
        end = ce - 200
        for o in placed:
            if o is not p and o.get("type") == "stat" and o["start"] >= p["start"]:
                end = min(end, o["start"] - CARD_PAD_MS)
        if end > p["end"]:
            p["end"] = end
        placed[:] = [
            o for o in placed
            if o is p or o.get("type") in ("stat", "chapter") or o.get("kind") == "quadrant"
            or o["end"] <= p["start"] + 50 or o["start"] >= p["end"] - 50
        ]
        break
    for p in sorted((x for x in placed if x.get("type") == "stat"), key=lambda x: int(x["start"])):
        spoken = p.get("spoken_end_abs")
        if spoken is None:
            continue
        floor = min(ce - 200, int(spoken))
        need = min(ce - 200, int(spoken) + STAT_END_PAD_MS)
        need = max(need, floor)
        blockers = [
            o["start"] for o in placed
            if o is not p and o.get("type") in ("stat", "chapter") and o["start"] > p["start"]
        ]
        if blockers:
            need = min(need, min(blockers) - CARD_PAD_MS)
        if need <= p["end"]:
            continue
        for o in list(placed):
            if o is p or o.get("type") in ("stat", "chapter"):
                continue
            if not (o["start"] < need and p["start"] < o["end"]):
                continue
            o["start"] = need + CARD_PAD_MS
            min_keep = 8000 if o.get("kind") == "quadrant" else 2500
            if o.get("kind") == "quadrant" and o.get("intro"):
                min_keep = QUADRANT_INTRO_MIN_MS
            if o["end"] - o["start"] < min_keep:
                o["end"] = min(ce - 200, o["start"] + min_keep)
            if o["end"] - o["start"] < 2500 or o["start"] >= ce - 400:
                placed.remove(o)
        p["end"] = max(p["end"], min(need, ce - 200))


# ---------------------------------------------------------------------------
# judgment / RAG sections and the recap always get one strong graphic
# ---------------------------------------------------------------------------

_RECAP_SEG_RE = re.compile(
    r"\b(recaps?|take-?aways?|wrap[- ]?up|what we learned|closing thoughts)\b", re.I,
)
_JUDGMENT_RAG_RE = re.compile(
    r"\b(judg(?:e)?ments?|retrieval[- ]augmented|\brag\b|retriev\w+|hallucin\w+)\b", re.I,
)
_DIAGRAM_SLOT_RE = re.compile(
    r"\b(diagrams?|flowcharts?|white ?boards?|schematics?|infographics?)\b", re.I,
)


def needs_strong_graphic(seg: dict[str, Any], lines: list[dict[str, Any]]) -> str | None:
    """'recap' or 'explain' when the segment must show a stat, diagram, bullets, or quadrant."""
    title_id = f"{seg.get('title') or ''} {seg.get('segment_id') or ''}"
    if _RECAP_SEG_RE.search(title_id):
        return "recap"
    blob = title_id + " " + " ".join(ln.get("text") or "" for ln in lines)
    if _JUDGMENT_RAG_RE.search(blob):
        return "explain"
    return None


def _has_strong_graphic(cards: list[dict[str, Any]]) -> bool:
    return any(c.get("type") in ("stat", "diagram", "bullets", "chart") for c in cards)


def synthesize_strong_graphic(
    seg: dict[str, Any], lines: list[dict[str, Any]], kind: str,
) -> dict[str, Any] | None:
    """A real card from this segment's own lines when the script left no visual seat."""
    items: list[str] = []
    for ln in lines:
        for sent in _sentences(ln.get("text") or ""):
            if len(sent) < 24:
                continue
            items.append(sent if kind == "recap" else _truncate(sent, 110))
            if len(items) >= 4:
                break
        if len(items) >= 4:
            break
    if len(items) < 2:
        return None
    if kind == "recap":
        dia = bullets_to_diagram("Key Takeaways", items, set())
        if dia:
            return {"type": "diagram", **dia}
    title = str(seg.get("title") or "How it fits")
    return {
        "type": "diagram", "kind": "pillars", "title": title, "center": "",
        "nodes": [{"label": it, "detail": ""} for it in items[:4]],
    }


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
        # Key Takeaways never shrink below ~8s. A quadrant intro never shrinks
        # below QUADRANT_INTRO_MIN_MS. Other cards may retry at 7s.
        floor = 8000 if (
            card["type"] == "diagram" and KEY_TAKEAWAYS_RE.search(str(card.get("title") or ""))
        ) else 7000
        floor = max(floor, int(card.get("min_dur") or 0))
        if dur > floor:
            return _place({**card, "dur": floor}, lo, hi, busy)
        return None
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
    factcheck: dict[str, Any] | None = None,
    log: Callable[[str], None] = lambda m: print(m, file=sys.stderr),
) -> dict[str, Any]:
    segs = episode.get("segments") or []
    topic = str(episode.get("topic") or "")
    if factcheck and (factcheck.get("issues") or []):
        try:
            from factcheck_episode import sync_cards_to_factcheck
            n_cards = sync_cards_to_factcheck(episode, list(factcheck.get("issues") or []))
            if n_cards:
                log(f"[plan] factcheck card copy updated on {n_cards} fields")
        except Exception as exc:  # never fatal
            log(f"[plan] factcheck card sync failed: {exc!r}")
    seg_lines = [_line_windows(seg, durations, int(t["start_ms"])) for seg, t in zip(segs, seg_timings)]
    seg_durs = [int(t["duration_ms"]) for t in seg_timings]
    stats = collect_stats(episode, seg_lines)
    chapters = pick_chapters(episode, seg_lines, seg_durs, total_ms, stats, clean_title)
    script_quads: list[dict[str, Any]] = []
    try:
        script_quads = script_quadrant_seats(episode, seg_lines)
    except Exception as exc:  # never fatal
        log(f"[plan] script quadrants failed: {exc!r}")
        script_quads = []
    quad = None
    if script_quads:
        intro_sq = next(sq for sq in script_quads if sq.get("intro"))
        quad = {
            "x_axis": intro_sq["x_axis"], "y_axis": intro_sq["y_axis"], "cells": intro_sq["cells"],
            "source": "script", "intro": (intro_sq["seg_idx"], intro_sq["line_idx"]),
            "positions": {}, "presence": {}, "consumed": set(),
            "title": intro_sq.get("title") or "",
        }
    else:
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
    prev_edge_value = ""
    per_segment: list[dict[str, Any]] = []
    report = {"segments": [], "stats": [], "chapters": [], "broll": [], "backdrops": [], "dropped": [],
              "quadrant": None}
    if quad:
        report["quadrant"] = {"source": quad["source"], "x_axis": quad["x_axis"]["label"],
                              "y_axis": quad["y_axis"]["label"],
                              "cells": [{"label": c["label"], "x": c["x"], "y": c["y"]} for c in quad["cells"]],
                              "intro_segment": quad["intro"][0] + 1,
                              "count": len(script_quads) or 1,
                              "seats": [{"segment": s["seg_idx"] + 1, "line": s["line_idx"] + 1,
                                         "intro": bool(s.get("intro")), "highlight": s.get("highlight"),
                                         "title": s.get("title") or ""} for s in script_quads]}
        log(f"[plan] 2x2 quadrant x{len(script_quads) or 1}: {quad['x_axis']['label']} x {quad['y_axis']['label']} -> "
            + ", ".join(c["label"] for c in quad["cells"]) + f" ({quad['source']})")
    salt = 0
    max_gap_ms = 0

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
            spoken_end = _stat_spoken_end_ms(st["value"], ln, words_by_seg.get(si, [])) if lines else None
            cards.append({"type": "stat", "label": st["label"], "value": st["value"],
                          "subtext": st["subtext"], "companions": st["companions"],
                          "is_range": st["is_range"], "static": st["is_range"], "land_abs": land_abs,
                          "spoken_end_abs": spoken_end,
                          "desired": desired, "prio": 1,
                          "max_shift": 16000, "max_early": STAT_MAX_EARLY_MS,
                          "speakerColor": _dominant_color(lines, ln["idx"], ln["idx"], color_a, color_b)})
        # Every script quadrant overlay is its own 2x2. detect_quadrant() only
        # returns the first, which is why the other five never drew a matrix.
        if script_quads:
            for sq in script_quads:
                if sq["seg_idx"] != si or not lines:
                    continue
                il = max(0, min(len(lines) - 1, int(sq["line_idx"])))
                hl = sq.get("highlight")
                title = str(sq.get("title") or "")
                if not title and hl is not None:
                    title = sq["cells"][hl]["label"]
                intro = bool(sq.get("intro"))
                qc = quadrant_card(sq, title=title, highlight=hl)
                cards.append({
                    **qc, "intro": intro,
                    "min_dur": QUADRANT_INTRO_MIN_MS if intro else QUADRANT_HIGHLIGHT_MS,
                    "desired": lines[il]["start"], "prio": 2, "max_shift": 30000,
                    "max_early": 20000 if intro else 8000,
                    "speakerColor": _dominant_color(lines, il, il, color_a, color_b),
                })
        elif quad and quad["intro"][0] == si and lines:
            il = max(0, min(len(lines) - 1, quad["intro"][1]))
            cards.append({**quadrant_card(quad), "intro": True, "min_dur": QUADRANT_INTRO_MIN_MS,
                          "desired": lines[il]["start"], "prio": 2, "max_shift": 30000,
                          "max_early": 20000, "speakerColor": _dominant_color(lines, il, il, color_a, color_b)})
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
            if is_close_segment(seg, si, len(segs)):
                cta = close_cta_card(seg, lines, ov)
                cards.append({**cta, "desired": lines[-1]["start"] if lines else desired, "prio": 2,
                              "max_early": 12000, "max_shift": 8000, "speakerColor": color})
                continue
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

        if is_close_segment(seg, si, len(segs)) and not any(c.get("role") == "close_cta" for c in cards) and lines:
            cta = close_cta_card(seg, lines, None)
            cards.append({**cta, "desired": lines[-1]["start"], "prio": 2, "max_early": 12000,
                          "max_shift": 8000, "speakerColor": color_a})

        if not _has_strong_graphic(cards):
            why = needs_strong_graphic(seg, lines)
            if why:
                extra = synthesize_strong_graphic(seg, lines, why)
                if extra:
                    anchor_i = min(1, len(lines) - 1) if lines else 0
                    extra.update({
                        "desired": lines[anchor_i]["start"] if lines else cs,
                        "prio": 2,
                        "speakerColor": _dominant_color(lines, 0, anchor_i, color_a, color_b) if lines else color_a,
                    })
                    cards.append(extra)
                    log(f"[plan] strong graphic ({why}) for segment {si + 1}: {extra.get('title') or extra.get('kind')}")

        before_dedupe = len(cards)
        cards = dedupe_adjacent_stat_cards(cards)
        if len(cards) < before_dedupe:
            report["dropped"].append({
                "segment": si + 1, "type": "stat", "title": "duplicate value", "reason": "adjacent beat",
            })
        if prev_edge_value:
            ordered = sorted(cards, key=lambda c: (int(c.get("desired") or 0), int(c.get("prio") or 9)))
            for c in ordered:
                if c.get("type") == "chapter":
                    continue
                if c.get("type") == "stat" and stat_values_collide(prev_edge_value, str(c.get("value") or "")):
                    cards = [x for x in cards if x is not c]
                    report["dropped"].append({
                        "segment": si + 1, "type": "stat", "title": c.get("value"),
                        "reason": "adjacent-segment duplicate",
                    })
                break

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

        # A scripted FlowDiagram / comparison / timeline is a Remotion diagram.
        # If placement could not fit it, force a short one instead of leaving
        # the slot to whiteboard or template B-roll.
        if not any(p.get("type") == "diagram" for p in placed):
            for v in seg.get("visuals") or []:
                if not isinstance(v, dict):
                    continue
                promised = _visual_to_card(v)
                if not promised or promised.get("type") != "diagram":
                    continue
                dur = min(9000, max(6000, (ce - cs) // 2))
                start = cs + 200
                for p in list(placed):
                    if p["type"] == "stat" and p["start"] < start + dur and start < p["end"]:
                        start = p["end"] + CARD_PAD_MS
                end = min(ce - 300, start + dur)
                if end - start < 5000:
                    break
                placed = [
                    p for p in placed
                    if p["type"] == "stat" or p["end"] <= start or p["start"] >= end
                ]
                placed.append({**promised, "start": start, "end": end, "speakerColor": color_a})
                log(f"[plan] forced Remotion diagram in segment {si + 1} ({promised.get('kind')})")
                break

        _seal_segment_holds(placed, cards, cs, ce, seg, si, len(segs), color_a)
        busy = [(int(p["start"]), int(p["end"])) for p in placed if p.get("type") != "broll"]

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
            slot_blob = f"{wtext} {' '.join(queries[:4])}"
            # Diagram-shaped windows reject whiteboard / PROMOTION-GROWTH slides.
            role = "diagram" if _DIAGRAM_SLOT_RE.search(slot_blob) else "broll"
            # Reject on the window text only. Segment context ("supply chain"
            # elsewhere) must not keep a port photo under a flavor beat.
            photo = picker.pick(queries, context_text=wtext, role=role) if picker else None
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

        fill_graphics_gaps(
            placed, cs, ce, lines, color_a,
            seg_words=seg_words, seg_text=seg_text, picker=picker,
        )

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
            photo = picker.pick(qs, context_text=btext, role="backdrop") if picker else None
            if photo:
                backdrops.append({"start": b0, "end": b1, "photo_url": photo["url"], "query": photo["query"], "photo_id": photo["id"]})
                report["backdrops"].append({"segment": si + 1, "query": photo["query"], "photo_id": photo["id"]})

        # --- rebase to card-relative ms ---
        beats = []
        for p in sorted(placed, key=lambda c: c["start"]):
            b = {k2: v2 for k2, v2 in p.items() if k2 not in (
                "start", "end", "desired", "prio", "dur", "max_shift", "max_early",
                "land_abs", "spoken_end_abs", "min_dur", "hold_to_end",
            )}
            b["show_ms"] = int(p["start"] - cs)
            b["hide_ms"] = int(p["end"] - cs)
            if b["type"] == "stat":
                # Card-relative ms of the spoken number: StatHero finishes any
                # count-up by then (snaps if the card started too late).
                b["land_ms"] = int(p.get("land_abs", p["start"]) - cs)
                if p.get("spoken_end_abs") is not None:
                    b["spoken_end_ms"] = int(p["spoken_end_abs"] - cs)
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
        gap_ms = longest_graphics_gap_ms(beats, ce - cs)
        max_gap_ms = max(max_gap_ms, gap_ms)
        longest_static = gap_ms
        content_cards = [p for p in sorted(placed, key=lambda c: c["start"]) if p.get("type") != "broll"]
        prev_edge_value = (
            str(content_cards[-1].get("value") or "")
            if content_cards and content_cards[-1].get("type") == "stat" else ""
        )
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
        "max_graphics_gap_s": round(max_gap_ms / 1000, 1),
        "close_cta": sum(1 for s in per_segment for b in s["beats"] if b.get("role") == "close_cta"),
    }
    report["stat_inventory"] = sync_stat_inventory(episode, stats, per_segment)
    report["summary"]["stats_missing"] = len(report["stat_inventory"]["missing"])
    return {"segments": per_segment, "report": report}


__all__ = ["plan_episode", "collect_stats", "pick_chapters", "bullets_to_diagram", "stat_signature",
           "stat_is_static", "enrich_stat_value", "normalize_stat_display", "transition_final_value",
           "apply_multiplier_symbol", "is_bare_unitless", "dedupe_adjacent_stat_cards",
           "unique_chapter_subtitle", "needs_strong_graphic", "synthesize_strong_graphic",
           "detect_quadrant", "normalize_quadrant", "stat_hold_ms",
           "script_quadrant_seats", "is_close_segment", "close_cta_card",
           "fill_graphics_gaps", "sync_stat_inventory", "longest_graphics_gap_ms",
           "MAX_GRAPHICS_GAP_S", "QUADRANT_INTRO_MIN_MS", "STAT_END_PAD_MS"]
