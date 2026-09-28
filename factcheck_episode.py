"""Durable fact-check step for primary-source HBR / framework listens.

Used by research_podcast (when a source file is present) and Module-5 prep
scripts. Mirrors the Enterprise factcheck_report.json contract:
  - episode.pre_factcheck.json backup
  - factcheck_report.json with issues/summary
  - rewritten episode.json when unsupported claims are fixed
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

NUM_RE = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})*(?:\.\d+)?%?|\d+%|\$\d[\d,]*(?:\.\d+)?)"
)


def normalize_num(s: str) -> str:
    return s.replace(",", "").strip().lower()


def local_scan(episode: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    """Flag numeric tokens in dialogue that never appear in the source text."""
    src_norm = source_text.lower().replace(",", "")
    src_nums = {normalize_num(m.group(1)) for m in NUM_RE.finditer(source_text)}
    issues: list[dict[str, Any]] = []
    for seg in episode.get("segments") or []:
        for ln in seg.get("lines") or []:
            text = ln.get("text") or ""
            for m in NUM_RE.finditer(text):
                tok = m.group(1)
                n = normalize_num(tok)
                if re.fullmatch(r"20[2-3]\d", n):
                    continue
                if n in src_nums or n.rstrip("%") in src_nums:
                    continue
                if n.endswith("%") and n[:-1] in src_nums:
                    continue
                if n in src_norm or tok.lower() in source_text.lower():
                    continue
                issues.append(
                    {
                        "line_id": ln.get("line_id"),
                        "claim": tok,
                        "supported": False,
                        "reason": f"number/token {tok!r} not found in source text",
                        "corrected_text": None,
                        "method": "local_scan",
                    }
                )
    return issues


def _script_lines(episode: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for seg in episode.get("segments") or []:
        for ln in seg.get("lines") or []:
            out.append(
                {
                    "segment_id": seg.get("segment_id"),
                    "line_id": ln.get("line_id"),
                    "host_id": ln.get("host_id"),
                    "text": ln.get("text"),
                }
            )
    return out


FACTCHECK_PROMPT = """You are a strict fact-checker for a podcast script about a primary-source article (often HBR).
SOURCE TEXT (sole allowed ground truth):
\"\"\"
{source}
\"\"\"

SCRIPT LINES (JSON array):
{lines}

TASK:
1. Find every concrete claim that is a number, ranking, named company/org/product,
   statistic, date, or specific case-study figure (including failure rates and model accuracy %).
2. supported=true only if clearly present or a faithful paraphrase of the SOURCE.
   A claim that distorts the source (wrong number, wrong company, wrong direction,
   overstated certainty) is supported=false.
3. If unsupported, provide corrected_text for the WHOLE line that removes/replaces the
   invented detail with something grounded (or softens to a non-specific claim the source
   supports). Never invent new stats. Do not speak host names.
4. List EVERY claim you checked in "claims" (supported and unsupported), one entry per
   claim, keep "claim" under 12 words and "evidence" to a short source quote (<= 20 words).
5. Return ONLY JSON:
{{"claims":[{{"line_id":"...","claim":"...","supported":true,"evidence":"..."}}],
 "issues":[{{"line_id":"...","claim":"...","supported":false,"reason":"...","corrected_text":"..."}}],
 "summary":{{"checked_claims":0,"unsupported":0,"rewritten":0}}}}
"""


def gemini_factcheck(episode: dict[str, Any], source_text: str) -> dict[str, Any]:
    """Gemini claim check with model/key fallback (gemini_util).

    2026-09-28: never "passes" silently. checked_claims is derived from the
    returned claims list; when every model/key fails the summary carries
    status="warning_unchecked" and the attempts, instead of a quiet 0.
    """
    from gemini_util import GeminiUnavailable, gemini_keys, generate_json

    if not gemini_keys():
        return {
            "issues": [],
            "claims": [],
            "summary": {"checked_claims": 0, "unsupported": 0, "rewritten": 0,
                        "gemini": "skipped_no_key", "status": "warning_unchecked"},
        }
    prompt = FACTCHECK_PROMPT.format(
        source=source_text[:90000],
        lines=json.dumps(_script_lines(episode), ensure_ascii=False),
    )

    def _valid(obj: Any) -> bool:
        return isinstance(obj, dict) and isinstance(obj.get("issues", []), list) and (
            len(obj.get("claims") or []) > 0 or len(obj.get("issues") or []) > 0
        )

    try:
        result, meta = generate_json(prompt, max_output_tokens=32768, validate=_valid)
    except GeminiUnavailable as exc:
        return {
            "issues": [],
            "claims": [],
            "summary": {"checked_claims": 0, "unsupported": 0, "rewritten": 0,
                        "gemini": f"failed:{exc}", "status": "warning_unchecked",
                        "gemini_attempts": exc.attempts},
        }
    claims = [c for c in (result.get("claims") or []) if isinstance(c, dict)]
    issues = [i for i in (result.get("issues") or []) if isinstance(i, dict)]
    # Unsupported claims listed only in "claims" still count as issues.
    seen = {(i.get("line_id"), i.get("claim")) for i in issues}
    for c in claims:
        if c.get("supported") is False and (c.get("line_id"), c.get("claim")) not in seen:
            issues.append({**c, "corrected_text": c.get("corrected_text")})
    checked = max(len(claims), len(issues))
    return {
        "issues": issues,
        "claims": claims,
        "summary": {
            "checked_claims": checked,
            "unsupported": sum(1 for i in issues if i.get("supported") is False),
            "rewritten": 0,
            "gemini": f"ok:{meta.get('model')}",
            "gemini_attempts": meta.get("attempts"),
            "status": "ok" if checked > 0 else "warning_unchecked",
        },
    }


def apply_corrections(episode: dict[str, Any], issues: list[dict[str, Any]]) -> int:
    by_id = {
        i["line_id"]: i
        for i in issues
        if i.get("line_id") and i.get("supported") is False and i.get("corrected_text")
    }
    rewritten = 0
    for seg in episode.get("segments") or []:
        for ln in seg.get("lines") or []:
            lid = ln.get("line_id")
            if lid in by_id:
                ln["text"] = by_id[lid]["corrected_text"]
                rewritten += 1
    return rewritten


def find_source_text(out_dir: Path, explicit: str | Path | None = None) -> str | None:
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
    out_dir = Path(out_dir)
    candidates = [
        out_dir / "hbr_source.md",
        out_dir / "source.txt",
        out_dir / "source.md",
    ]
    candidates.extend(sorted(out_dir.glob("*_source.txt")))
    candidates.extend(sorted(out_dir.glob("*_source.md")))
    for c in candidates:
        if c.exists() and c.stat().st_size > 200:
            return c.read_text(encoding="utf-8", errors="replace")
    return None


def fact_check_episode(
    episode: dict[str, Any],
    source_text: str,
    *,
    out_dir: Path | None = None,
    use_gemini: bool = True,
    apply_fixes: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (possibly rewritten episode, report). Writes artifacts when out_dir set."""
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "episode.pre_factcheck.json").write_text(
            json.dumps(episode, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    local_issues = local_scan(episode, source_text)
    report: dict[str, Any]
    if use_gemini:
        report = gemini_factcheck(episode, source_text)
        if not isinstance(report, dict):
            report = {"issues": [], "summary": {}}
        issues = list(report.get("issues") or [])
        # Merge local-only findings not already present
        seen = {(i.get("line_id"), i.get("claim")) for i in issues}
        for li in local_issues:
            key = (li.get("line_id"), li.get("claim"))
            if key not in seen:
                issues.append(li)
        report["issues"] = issues
    else:
        report = {
            "issues": local_issues,
            "summary": {
                "checked_claims": len(local_issues),
                "unsupported": len(local_issues),
                "rewritten": 0,
                "method": "local_scan_only",
                "status": "local_only",
            },
        }

    rewritten = apply_corrections(episode, report.get("issues") or []) if apply_fixes else 0
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    summary["rewritten"] = rewritten
    summary["local_scan_flags"] = len(local_issues)
    # 2026-09-28: 0 checked claims is a WARNING, never a silent pass.
    if not summary.get("checked_claims"):
        summary["status"] = "warning_unchecked"
    summary.setdefault("status", "ok")
    report["summary"] = summary

    if out_dir is not None:
        (out_dir / "factcheck_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (out_dir / "episode.json").write_text(
            json.dumps(episode, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return episode, report


def factcheck_status_line(report: dict[str, Any]) -> str:
    """One-line status for logs; starts with WARNING when nothing was checked."""
    s = (report or {}).get("summary") or {}
    status = s.get("status") or "unknown"
    base = (f"checked={s.get('checked_claims', 0)} unsupported={s.get('unsupported', 0)} "
            f"rewritten={s.get('rewritten', 0)} local_flags={s.get('local_scan_flags', 0)} "
            f"gemini={str(s.get('gemini', ''))[:80]}")
    if status == "warning_unchecked":
        return f"WARNING fact-check UNCHECKED (0 claims verified) -- {base}"
    return f"status={status} {base}"
