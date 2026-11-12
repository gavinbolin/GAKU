"""Match AI Strategy v2 rewatch fixes (2026-10-09). Forward-only planner rules.

Run: python tests/test_match_ai_v2_fixes.py
Offline: no Pexels, Gemini, Kokoro, or whisper. The episode check reads the
review folder and writes only to a temp directory.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import broll_queries as bq  # noqa: E402

REVIEW = Path(
    r"C:\Users\gavin\.PROJECT\PROJECT GAKU\output\gaku-review"
    r"\20261009_0922_08.10.26_Match_AI_Strategy_Bouque_v2_rewatch"
)
METALENS_CORRECTION = (
    "At the same time, Apple designed AI-optimized metalenses to replace traditional "
    "camera lenses, successfully integrating materials science and semiconductor "
    "manufacturing to file dozens of patents within two years."
)


def _plan_small(ep, line_ms=5000):
    import visual_timeline as vt

    durations: dict[str, int] = {}
    seg_timings = []
    cursor = 0
    for seg in ep["segments"]:
        start = cursor
        for ln in seg.get("lines") or []:
            durations[ln["line_id"]] = line_ms
            cursor += line_ms + int(ln.get("pause_after_ms") or 0)
        seg_timings.append({"start_ms": start, "end_ms": cursor, "duration_ms": cursor - start})
    windows = [(t["start_ms"], t["end_ms"]) for t in seg_timings]
    return vt.plan_episode(
        episode=ep,
        durations=durations,
        seg_timings=seg_timings,
        card_windows=windows,
        word_timings=[],
        color_a="#F59E0B",
        color_b="#10B981",
        total_ms=cursor or 1,
        picker=None,
        clean_title=lambda t: t,
        complete_items=lambda _title, items: list(items),
        log=lambda _m: None,
    )


def _quadrant(title, highlight=None, line=2):
    ov = {
        "type": "quadrant",
        "title": title,
        "show_at_line": line,
        "x_axis": {"label": "Value-Chain Control", "low": "Low", "high": "High"},
        "y_axis": {"label": "Technological Breadth", "low": "Low", "high": "High"},
        "cells": [
            {"label": "Focused Differentiation", "x": "low", "y": "low"},
            {"label": "Vertical Integration", "x": "high", "y": "low"},
            {"label": "Collaborative Ecosystem", "x": "low", "y": "high"},
            {"label": "Platform Leadership", "x": "high", "y": "high"},
        ],
    }
    if highlight:
        ov["highlight"] = highlight
    return ov


def _lines(seg, n, text):
    return [
        {"line_id": f"{seg}_l{k}", "host_id": "A" if k % 2 == 0 else "B", "text": text}
        for k in range(n)
    ]


def test_quadrant_every_overlay_is_a_2x2_and_intro_holds():
    import visual_timeline as vt

    ep = {"topic": "t", "segments": [
        {"segment_id": "s01", "title": "Open", "lines": _lines("s01", 4, "Cold open on the two axes before the matrix.")},
        {"segment_id": "s02", "title": "Four strategies", "lines": _lines("s02", 8, "Both axes produce four strategies you can name."),
         "overlays": [_quadrant("The Four AI Innovation Strategies", line=4)]},
        {"segment_id": "s03", "title": "Focused Differentiation", "lines": _lines("s03", 8, "This cell is focused differentiation in practice."),
         "overlays": [_quadrant("Focused Differentiation", "Focused Differentiation", 2)]},
    ]}
    seats = vt.script_quadrant_seats(ep, [[{"idx": i} for i in range(8)] for _ in ep["segments"]])
    assert len(seats) == 2 and seats[0]["intro"] and not seats[1]["intro"]
    plan = _plan_small(ep, 5000)
    quads = [b for s in plan["segments"] for b in s["beats"] if b.get("kind") == "quadrant"]
    assert len(quads) == 2, [(b.get("title"), b.get("show_ms"), b.get("hide_ms")) for b in quads]
    for b in quads:
        q = b["quadrant"]
        assert len(q["cells"]) == 4, q
        assert q["x_axis"]["label"] and q["y_axis"]["label"]
    intro = next(b for b in quads if b["quadrant"].get("highlight") is None)
    assert intro["hide_ms"] - intro["show_ms"] >= 8000, intro
    assert intro["hide_ms"] - intro["show_ms"] >= vt.QUADRANT_INTRO_MIN_MS - 1 or intro["hide_ms"] - intro["show_ms"] >= 8000


def test_graphics_gap_is_capped_at_18s():
    import visual_timeline as vt

    text = "Focused differentiation keeps a team inside one niche they can actually run."
    ep = {"topic": "t", "segments": [
        {"segment_id": "s01", "title": "Open", "lines": _lines("s01", 2, text)},
        {"segment_id": "s02", "title": "Focused Differentiation", "lines": _lines("s02", 12, text)},
        {"segment_id": "s03", "title": "End", "lines": _lines("s03", 2, text)},
    ]}
    plan = _plan_small(ep, 5000)
    beats = plan["segments"][1]["beats"]
    assert any(b.get("role") == "gap_fill" for b in beats), beats
    assert vt.longest_graphics_gap_ms(beats, 12 * 5000) <= vt.MAX_GRAPHICS_GAP_S * 1000
    assert vt.MAX_GRAPHICS_GAP_S == 18


def test_factcheck_card_uses_patents_not_integrated():
    import factcheck_episode as fc

    ep = {"segments": [{"segment_id": "s01_hook", "lines": [
        {"line_id": "s01_hook_l03", "text": "Apple designed metalenses."},
    ], "overlays": [
        {"type": "bullets", "title": "A Tale of Two Systems", "items": [
            "GM: 40% lighter bracket stalled by stamped-steel supply chain",
            "Apple: AI-optimized metalens integrated in under two years",
        ]},
        {"type": "quadrant", "cells": [{"label": "Vertical Integration", "x": "high", "y": "low"}]},
    ]}]}
    issues = [{
        "line_id": "s01_hook_l03",
        "supported": False,
        "claim": "Apple launched metalenses technology within just two years.",
        "corrected_text": METALENS_CORRECTION,
    }]
    assert fc.sync_cards_to_factcheck(ep, issues) >= 1
    item = ep["segments"][0]["overlays"][0]["items"][1]
    assert "patents" in item.lower(), item
    assert re.search(r"\bintegrated\b", item, re.I) is None, item
    assert re.search(r"\blaunched\b", item, re.I) is None, item
    assert ep["segments"][0]["overlays"][0]["items"][0].startswith("GM")
    assert ep["segments"][0]["overlays"][1]["cells"][0]["label"] == "Vertical Integration"
    assert fc.sync_cards_to_factcheck(ep, issues) == 0
    # apply_corrections wires the same card sync
    ep2 = json.loads(json.dumps(ep))
    ep2["segments"][0]["overlays"][0]["items"][1] = "Apple: AI-optimized metalens integrated in under two years"
    fc.apply_corrections(ep2, issues)
    assert "patents" in ep2["segments"][0]["overlays"][0]["items"][1].lower()


def test_close_segment_seats_its_own_cta():
    import visual_timeline as vt

    assert vt.is_close_segment({"segment_id": "s10_close", "title": "Stop Piloting, Start Leading"}, 9, 10)
    assert not vt.is_close_segment({"segment_id": "s09_recap", "title": "Key Takeaways"}, 8, 10)
    assert not vt.is_close_segment({"segment_id": "s03", "title": "Accenture's Gains"}, 2, 3)
    close_lines = _lines("s10", 6, "We keep unpacking the operating model for a while here today.")
    close_lines[-1]["text"] = "It is time to stop piloting and start leading."
    ep = {"topic": "t", "segments": [
        {"segment_id": "s01", "title": "Opening", "lines": _lines("s01", 2, "Cold open on the factory floor at dawn today.")},
        {"segment_id": "s09_recap", "title": "Key Takeaways",
         "lines": _lines("s09", 4, "The lesson is to match the call to the system you can run."),
         "overlays": [{"type": "bullets", "title": "Key Takeaways", "show_at_line": 1,
                       "items": ["Know your quadrant", "Align ambition", "Build the system"]}]},
        {"segment_id": "s10_close", "title": "Stop Piloting, Start Leading", "lines": close_lines,
         "overlays": [{"type": "bullets", "title": "From Pilot to Leader", "show_at_line": 1, "items": [
             "Map value-chain control", "Assess technological breadth",
             "Select your strategic quadrant", "Build the systems to scale",
         ]}]},
    ]}
    plan = _plan_small(ep, 4000)
    close = plan["segments"][-1]["beats"]
    cta = [b for b in close if b.get("role") == "close_cta"]
    assert cta, [b.get("type") for b in close]
    assert "takeaway" not in cta[0]["title"].lower(), cta[0]
    blob = json.dumps(cta).lower()
    assert "stop piloting" in blob
    assert "…" not in json.dumps(cta)
    recap = json.dumps(plan["segments"][1]["beats"])
    assert "Key Takeaways" in recap


def test_broll_prefers_the_beat_and_rejects_unrelated_domains():
    window = (
        "McCormick and Company used SAGE to speed flavor development "
        "across the global spice trade."
    )
    name, qs = bq.named_case_queries(window)
    assert name == "mccormick", (name, qs)
    blob = " ".join(qs).lower()
    assert any(w in blob for w in ("spice", "seasoning", "herb")), qs
    assert "port" not in blob and "forklift" not in blob and "crane" not in blob
    local = bq.local_query_candidates(
        window, "PepsiCo supply chain shipping containers warehouse logistics",
    )
    assert local, local
    assert not any("port" in q or "forklift" in q or "shipping container" in q for q in local), local
    assert bq.photo_rejected(
        {"alt": "cargo crane loading a container ship at the port", "keywords": ["gantry crane"]},
        window,
    )
    assert bq.photo_rejected(
        {"alt": "cargo crane loading a container ship at the port"},
        "containers leaving the port on a cargo ship",
    ) is None
    assert bq.photo_rejected({"alt": "chef tasting spices in a laboratory"}, window) is None
    # existing rejects stay
    assert bq.photo_rejected({"alt": "Orange industrial robot arm in a factory"}, window)
    assert bq.photo_rejected({"alt": "Double exposure portrait of a businessman"}, window)


def test_qualified_stat_value_and_spoken_end():
    import visual_queue
    import visual_timeline as vt

    filler = "We keep unpacking how the operating model changes day to day work for teams."
    lines = _lines("s02", 10, filler)
    lines[4]["text"] = "Novartis cut trial design time by more than 30 percent in that program."
    ep = {
        "topic": "t",
        "hosts": [{"id": "A", "voice_id": "am_michael"}, {"id": "B", "voice_id": "am_michael"}],
        "segments": [
            {"segment_id": "s01", "title": "Open", "lines": _lines("s01", 2, filler)},
            {"segment_id": "s02", "title": "Novartis", "lines": lines, "overlays": [
                {"type": "stat", "label": "Trial design", "value": "30%", "show_at_line": 5, "hide_at_line": 6},
            ], "visuals": [
                {"template": "StatCallout", "start_with_line": lines[6]["line_id"],
                 "data": {"value": 17000, "label": "Homes sold out of 27000 purchased"}},
            ]},
            {"segment_id": "s03", "title": "End", "lines": _lines("s03", 2, filler)},
        ],
    }
    # The 17000 figure has to be spoken or collect_stats drops it.
    lines[6]["text"] = "Zillow sold 17000 homes out of 27000 purchased before the write-down."
    clips = {"clips": [
        {"line_id": ln["line_id"], "duration_ms": 6000}
        for s in ep["segments"] for ln in s["lines"]
    ]}
    with tempfile.TemporaryDirectory() as td:
        m = visual_queue.build_visual_manifest(ep, clips, Path(td), pexels_key=None, audio_path=None)
        report = json.loads((Path(td) / "visual_plan_report.json").read_text(encoding="utf-8"))
        saved = json.loads((Path(td) / "episode.json").read_text(encoding="utf-8"))
    beats = [b for v in m["visuals"] if v["template"] == "SegmentCard" for b in v["data"].get("beats") or []]
    stat = next(b for b in beats if isinstance(b.get("value"), str) and "30%" in b["value"])
    assert stat["value"] == "More than 30%", stat
    assert stat.get("spoken_end_ms") is not None, stat
    assert stat["hide_ms"] >= stat["spoken_end_ms"], stat
    vals = [str(o.get("value")) for s in saved["segments"] for o in (s.get("overlays") or []) if o.get("type") == "stat"]
    assert "More than 30%" in vals, vals
    inv = report["stat_inventory"]
    missing_blob = json.dumps(inv["missing"]).replace(",", "")
    assert "17000" not in missing_blob, inv["missing"]
    rendered_blob = json.dumps(inv["rendered"]).replace(",", "")
    assert "17000" in rendered_blob, inv["rendered"]
    assert "More than 30%" in rendered_blob or "30%" in rendered_blob
    # spoken tail includes the word "percent", not only the digits
    ln = {"idx": 4, "text": lines[4]["text"], "start": 0, "end": 6000}
    words = []
    parts = ln["text"].split()
    step = 6000 / len(parts)
    for i, w in enumerate(parts):
        words.append({"word": w, "line_idx": 4, "start_ms": int(i * step), "end_ms": int((i + 1) * step)})
    end = vt._stat_spoken_end_ms("More than 30%", ln, words)
    percent = next(w for w in words if w["word"].lower().startswith("percent"))
    assert end is not None and end >= percent["end_ms"] - 5, (end, percent)


def test_offline_match_ai_v2_episode():
    """Planner against the rewatch episode.json. No network, no writes to that folder."""
    import visual_queue

    ep_path = REVIEW / "episode.json"
    clips_path = REVIEW / "clip_durations.json"
    fc_path = REVIEW / "factcheck_report.json"
    assert ep_path.is_file(), ep_path
    assert clips_path.is_file(), clips_path
    assert fc_path.is_file(), fc_path
    episode = json.loads(ep_path.read_text(encoding="utf-8"))
    clips = json.loads(clips_path.read_text(encoding="utf-8"))
    before = ep_path.read_bytes()
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "factcheck_report.json").write_text(fc_path.read_text(encoding="utf-8"), encoding="utf-8")
        m = visual_queue.build_visual_manifest(
            episode, clips, td_path, pexels_key=None, audio_path=None,
        )
        report = json.loads((td_path / "visual_plan_report.json").read_text(encoding="utf-8"))
        saved = json.loads((td_path / "episode.json").read_text(encoding="utf-8"))
    assert ep_path.read_bytes() == before

    beats = [b for v in m["visuals"] if v["template"] == "SegmentCard" for b in (v["data"].get("beats") or [])]
    quads = [b for b in beats if b.get("kind") == "quadrant"]
    assert len(quads) >= 6, len(quads)
    for b in quads:
        assert len((b.get("quadrant") or {}).get("cells") or []) == 4, b.get("title")
    intro = [b for b in quads if (b.get("quadrant") or {}).get("highlight") is None]
    assert intro, "first quadrant intro missing"
    assert max(b["hide_ms"] - b["show_ms"] for b in intro) >= 8000, intro

    assert report["summary"]["max_graphics_gap_s"] <= 18, report["summary"]
    for seg in report["segments"]:
        assert seg["longest_without_change_s"] <= 18.05, seg

    cards = [v for v in m["visuals"] if v["template"] == "SegmentCard"]
    close = cards[-1]
    assert "takeaway" not in close["data"]["title"].lower(), close["data"]["title"]
    cta = [b for b in close["data"]["beats"] if b.get("role") == "close_cta"]
    assert cta, [b.get("type") for b in close["data"]["beats"]]
    assert "takeaway" not in cta[0].get("title", "").lower()
    assert "stop piloting" in json.dumps(cta).lower()

    rendered = json.dumps(beats)
    assert "patents" in rendered.lower()
    assert re.search(r"metalens\w*[^\n]{0,80}\bintegrated\b", rendered, re.I) is None, "metalens card still says integrated"
    assert re.search(r"\bintegrated\b[^\n]{0,40}metalens", rendered, re.I) is None

    stat = next(b for b in beats if isinstance(b.get("value"), str) and "30%" in b["value"] and "more than" in b["value"].lower())
    assert stat["value"] == "More than 30%", stat["value"]
    assert stat.get("spoken_end_ms") is not None, stat
    assert stat["hide_ms"] >= stat["spoken_end_ms"], stat

    inv = report["stat_inventory"]
    missing_blob = json.dumps(inv["missing"]).replace(",", "").lower()
    rendered_blob = json.dumps(inv["rendered"]).replace(",", "").lower()
    assert "17000" not in missing_blob, inv["missing"]
    assert "17000" in rendered_blob, inv["rendered"]
    overlay_vals = " ".join(
        str(o.get("value"))
        for s in saved["segments"]
        for o in (s.get("overlays") or [])
        if isinstance(o, dict) and o.get("type") == "stat"
    )
    assert "More than 30%" in overlay_vals, overlay_vals
    assert "17000" in overlay_vals.replace(",", "")

    mc = [b for b in report["broll"] if b.get("named_case") == "mccormick"]
    assert mc, "expected McCormick / SAGE queries on that beat"
    for b in mc:
        q = (b.get("query") or "").lower()
        assert any(w in q for w in ("spice", "seasoning", "herb", "flavor", "flavour")), b
        assert "port" not in q and "crane" not in q and "forklift" not in q, b
    for b in report["broll"]:
        excerpt = b.get("excerpt") or ""
        if re.search(r"mccormick|\bsage\b|flavou?r|spice", excerpt, re.I):
            q = (b.get("query") or "").lower()
            assert "port" not in q and "crane" not in q and "forklift" not in q, b


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                fails += 1
                import traceback
                print(f"FAIL {name}: {exc!r}")
                traceback.print_exc()
    print(f"{'OK' if not fails else 'FAILED'} ({fails} failures)")
    raise SystemExit(1 if fails else 0)
