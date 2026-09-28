"""Hook-retell detector / fixer (2026-09-28, M7 review item 3).

The cold-open hook tells its story once; the next segment should reference it
briefly ("that GM-vs-Apple contrast") and move on. In M7 the GM seat bracket
and Apple metalens story was told in s01_hook and then retold almost verbatim
in s02 (40% lighter / 20% stronger, "compare that to Apple...").

detect_hook_retell(episode) -> report (never raises).
dedupe_hook_retell(episode, research_notes=None, use_gemini=True) -> (episode, report)
  If the overlap is heavy, it asks Gemini (model/key fallback via gemini_util)
  to rewrite ONLY the retelling lines of the next segment into a one-line
  callback + new material. line_ids, host_ids and line count stay the same, so
  overlay show_at_line indices remain valid. Any failure leaves the episode
  untouched and is recorded in the report. Additive to the prompt rules in
  synthesize_research_to_script.ts (outline + ALREADY_TOLD_IN_HOOK block).
"""
from __future__ import annotations

import copy
import json
import re
import sys
from typing import Any

_NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]+")
_STOP = set("""a an the and or but so of to in on at for with from by as is are was were be been being it its
this that these those they them their there here what which who whom whose when where why how than then
into over under about after before just also very really more most much many some any all not no yes do does did
have has had can could would should will may might must we you your our us i me my he she his her him
like well okay right sure think know mean thing things kind sort get got make made say said way lot even
because while still only such own same other another each every both either neither one two""".split())
_PROPER_SKIP = {"AI", "I", "So", "But", "And", "The", "That", "This", "Take", "Now", "Okay", "Right", "Yes",
                "Exactly", "Precisely", "Really", "Think", "Often", "Contrast", "Compare", "Within", "Around",
                "Studies", "Only", "It", "They", "What", "How", "Why", "A", "An", "In", "On", "For", "With",
                "Artificial", "Intelligence"}

RETELL_LINE_FRAC = 0.45     # >= this share of a line's content words appear in the hook
RETELL_MIN_WORDS = 6
HEAVY_RETELL_LINES = 3
SCAN_LINES = 12             # only the opening of the next segment is compared


def _content_words(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "") if w.lower() not in _STOP and len(w) > 2]


def _proper_nouns(text: str) -> set[str]:
    out: set[str] = set()
    for m in re.finditer(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z&\-]+(?:\s+[A-Z][a-zA-Z&\-]+)*)", text or ""):
        name = m.group(1)
        if name.split()[0] in _PROPER_SKIP:
            continue
        out.add(name.lower())
    return out


def _hook_index(segments: list[dict[str, Any]]) -> int | None:
    for i, s in enumerate(segments):
        if "hook" in str(s.get("segment_id", "")).lower() or str(s.get("kind", "")).lower() == "hook":
            return i
    return 0 if segments else None


def detect_hook_retell(episode: dict[str, Any]) -> dict[str, Any]:
    segs = episode.get("segments") or []
    hi = _hook_index(segs)
    rep: dict[str, Any] = {"heavy": False, "hook_segment": None, "next_segment": None,
                           "retell_lines": [], "shared_names": [], "shared_numbers": []}
    if hi is None or hi + 1 >= len(segs):
        return rep
    hook, nxt = segs[hi], segs[hi + 1]
    rep["hook_segment"], rep["next_segment"] = hook.get("segment_id"), nxt.get("segment_id")
    hook_text = " ".join(str(l.get("text") or "") for l in hook.get("lines") or [])
    hook_words = set(_content_words(hook_text))
    hook_nums = {n.rstrip(".,") for n in _NUM_RE.findall(hook_text) if not re.fullmatch(r"20[0-3]\d", n)}
    hook_names = _proper_nouns(hook_text)
    names: set[str] = set()
    nums: set[str] = set()
    for idx, ln in enumerate((nxt.get("lines") or [])[:SCAN_LINES]):
        text = str(ln.get("text") or "")
        cw = _content_words(text)
        line_names = _proper_nouns(text) & hook_names
        line_nums = {n.rstrip(".,") for n in _NUM_RE.findall(text)} & hook_nums
        frac = (sum(1 for w in cw if w in hook_words) / len(cw)) if cw else 0.0
        names |= line_names
        nums |= line_nums
        retell = (len(cw) >= RETELL_MIN_WORDS and frac >= RETELL_LINE_FRAC) or bool(line_nums) or ((
            bool(line_names) and (frac >= 0.3 or bool(rep["retell_lines"]))))
        if retell:
            rep["retell_lines"].append({"index": idx, "line_id": ln.get("line_id"), "overlap": round(frac, 2),
                                        "names": sorted(line_names), "numbers": sorted(line_nums)})
    rep["shared_names"], rep["shared_numbers"] = sorted(names), sorted(nums)
    n = len(rep["retell_lines"])
    rep["heavy"] = n >= HEAVY_RETELL_LINES or (n >= 2 and (len(names) >= 2 or bool(nums)))
    return rep


_REWRITE_PROMPT = """You are editing ONE segment of a two-host podcast script so it does not retell the cold-open hook.

HOOK (already heard by the listener -- do NOT retell it):
{hook}

NEXT SEGMENT (JSON lines; the ones listed in RETELL_LINE_IDS retell the hook):
{segment}

RETELL_LINE_IDS: {ids}

RESEARCH_NOTES (the only allowed source of facts):
{notes}

Rewrite ONLY the lines in RETELL_LINE_IDS. The first rewritten line may reference the hook in
ONE short callback phrase (e.g. "that GM-vs-Apple contrast"); the rest must move on to NEW
material from RESEARCH_NOTES that fits the segment title "{title}". Do not repeat the hook's
numbers or its case details. Keep each line's host_id and roughly its length (<= 36 words),
keep the conversation flowing into the unchanged lines, never invent statistics, never speak
host names, no markdown.
Return ONLY JSON: {{"lines":[{{"line_id":"...","text":"..."}}]}} with exactly the RETELL_LINE_IDS.
"""


def dedupe_hook_retell(
    episode: dict[str, Any],
    research_notes: str | None = None,
    *,
    use_gemini: bool = True,
    log=None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    log = log or (lambda s: print(f"[dedupe] {s}", file=sys.stderr))
    try:
        rep = detect_hook_retell(episode)
    except Exception as exc:  # noqa: BLE001
        return episode, {"heavy": False, "error": f"detect failed: {exc!r}", "rewritten": 0}
    rep["rewritten"] = 0
    if not rep.get("heavy"):
        rep["action"] = "none"
        return episode, rep
    log(f"hook retell detected in {rep['next_segment']}: {len(rep['retell_lines'])} lines "
        f"(names={rep['shared_names']}, numbers={rep['shared_numbers']})")
    if not use_gemini:
        rep["action"] = "flagged_only"
        return episode, rep
    try:
        from gemini_util import generate_json
        segs = episode["segments"]
        hi = _hook_index(segs)
        hook, nxt = segs[hi], segs[hi + 1]
        ids = [r["line_id"] for r in rep["retell_lines"]]
        prompt = _REWRITE_PROMPT.format(
            hook=" ".join(str(l.get("text") or "") for l in hook.get("lines") or []),
            segment=json.dumps([{k: l.get(k) for k in ("line_id", "host_id", "text")} for l in nxt.get("lines") or []],
                               ensure_ascii=False),
            ids=json.dumps(ids), notes=(research_notes or "(not provided; use only facts already in the script)")[:60000],
            title=nxt.get("title") or "",
        )

        def _valid(o: Any) -> bool:
            got = {str(x.get("line_id")) for x in (o or {}).get("lines") or [] if isinstance(x, dict) and x.get("text")}
            return set(map(str, ids)) <= got

        out, meta = generate_json(prompt, temperature=0.4, max_output_tokens=8192, validate=_valid, log=log)
        new_text = {str(x["line_id"]): str(x["text"]).strip() for x in out["lines"]}
        fixed = copy.deepcopy(episode)
        for ln in fixed["segments"][hi + 1].get("lines") or []:
            lid = str(ln.get("line_id"))
            if lid in new_text and new_text[lid]:
                rep.setdefault("changes", []).append({"line_id": lid, "before": ln.get("text"), "after": new_text[lid]})
                ln["text"] = new_text[lid]
                rep["rewritten"] += 1
        rep["action"] = "rewritten"
        rep["model"] = meta.get("model")
        after = detect_hook_retell(fixed)
        rep["after"] = {"heavy": after["heavy"], "retell_lines": len(after["retell_lines"])}
        return fixed, rep
    except Exception as exc:  # noqa: BLE001 -- never fatal
        rep["action"] = "flagged_only"
        rep["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        log(f"rewrite skipped: {rep['error']}")
        return episode, rep


__all__ = ["detect_hook_retell", "dedupe_hook_retell"]
