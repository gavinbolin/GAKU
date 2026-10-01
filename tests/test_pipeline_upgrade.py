"""Regression tests for the 2026-09-25 pipeline upgrade (daily review items 1-9).

Run:  python tests/test_pipeline_upgrade.py      (plain asserts, no pytest needed)
  or: python -m pytest tests/test_pipeline_upgrade.py
Offline: no Pexels / Gemini / Kokoro / whisper calls (whisper is faked).
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import broll_queries as bq  # noqa: E402
import tts_lexicon  # noqa: E402


# --------------------------------------------------------------------- item 6
def test_lexicon_substitution_rules():
    a = tts_lexicon.apply_lexicon
    assert a("Valentine, Politzer, and Davenport") == "Valentine, Pahlitzer, and Davenport"
    assert a("Politzer's article") == "Pahlitzer's article"
    assert a("POLITZER") == "PAHLITZER"
    assert a("Politzerian") == "Politzerian"  # whole word only
    assert a("Pulitzer Prize") == "Pulitzer Prize"
    assert a("Politzer", backend="some-other-backend") == "Politzer"


def test_lexicon_spoken_episode_is_a_copy():
    ep = {"segments": [{"lines": [{"line_id": "l1", "text": "Daniel Politzer wrote it."}]}]}
    sp = tts_lexicon.spoken_episode(ep, "kokoro")
    assert sp["segments"][0]["lines"][0]["text"] == "Daniel Pahlitzer wrote it."
    assert ep["segments"][0]["lines"][0]["text"] == "Daniel Politzer wrote it."


def test_collapse_spoken_tokens():
    words = [
        {"word": "Daniel", "start_ms": 0, "end_ms": 300},
        {"word": "Pahlitzer,", "start_ms": 300, "end_ms": 800},
        {"word": "and", "start_ms": 800, "end_ms": 900},
        {"word": "Palitzer", "start_ms": 900, "end_ms": 1300},  # whisper variant (heard_as)
        {"word": "Pah", "start_ms": 1300, "end_ms": 1500},      # split respelling
        {"word": "litzer", "start_ms": 1500, "end_ms": 1800},
    ]
    out = tts_lexicon.collapse_spoken_tokens(words)
    assert [w["word"] for w in out] == ["Daniel", "politzer", "and", "politzer", "politzer"], out
    assert out[1]["start_ms"] == 300 and out[1]["end_ms"] == 800
    assert out[4]["start_ms"] == 1300 and out[4]["end_ms"] == 1800


def _fake_whisper(spoken_words: list[tuple[str, int, int]]):
    mod = types.ModuleType("whisper_align")
    mod.align = lambda *_a, **_k: [{"word": w, "start_ms": s, "end_ms": e} for w, s, e in spoken_words]
    return mod


def test_captions_keep_written_spelling_and_timing():
    import visual_queue

    episode = {
        "topic": "Lexicon test",
        "segments": [{
            "segment_id": "s01", "title": "Intro",
            "lines": [
                {"line_id": "l1", "host_id": "A", "text": "The article by Daniel Politzer is great."},
                {"line_id": "l2", "host_id": "B", "text": "Politzer argues for platforms."},
            ],
        }],
    }
    clips = {"clips": [{"line_id": "l1", "duration_ms": 2600}, {"line_id": "l2", "duration_ms": 2000}]}
    # What whisper hears from the TTS copy ("Pahlitzer" / variant "Palitzer").
    heard = [("The", 0, 200), ("article", 200, 600), ("by", 600, 750), ("Daniel", 750, 1100),
             ("Pahlitzer", 1100, 1700), ("is", 1700, 1850), ("great.", 1850, 2400),
             ("Palitzer", 2700, 3300), ("argues", 3300, 3700), ("for", 3700, 3850),
             ("platforms.", 3850, 4500)]
    sys.modules["whisper_align"] = _fake_whisper(heard)
    try:
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "podcast.wav"
            wav.write_bytes(b"RIFF0000WAVE")  # only existence is checked (align is faked)
            words = visual_queue.compute_word_timings(episode, clips, audio_path=wav)
    finally:
        sys.modules.pop("whisper_align", None)
    shown = [w["word"] for w in words]
    assert "Politzer" in shown and not any(("ahlitzer" in w or "alitzer" in w) for w in shown), shown
    pol = [w for w in words if w["word"].startswith("Politzer")]
    assert pol[0]["start_ms"] == 1100 and pol[0]["end_ms"] == 1700, pol[0]
    sents = visual_queue.group_into_sentences(words)
    text = " ".join(w["word"] for s in sents for w in s["words"])
    assert "Daniel Politzer" in text and "Pahlitzer" not in text


# --------------------------------------------------------------------- item 3
def test_query_validator_bans_junk():
    for bad in ["Un", "AI", "Global", "enterprise AI", "strategic business", "Data integration",
                "industrial robot arm", "the of", "future technology"]:
        ok, why = bq.validate_query(bad)
        assert not ok, (bad, why)
    for good in ["airline check in counter", "bottling plant conveyor", "call center headset agent"]:
        ok, why = bq.validate_query(good)
        assert ok, (good, why)


def test_local_queries_are_grounded_and_robot_free():
    window = ("JetBlue built BlueBot, a knowledge bot on top of an LLM, so crew members "
              "and agents can ask questions about policies.")
    qs = bq.local_query_candidates(window, "Enterprise Gen AI JetBlue airline")
    assert qs, "expected fallback queries"
    for q in qs:
        assert bq.validate_query(q)[0], q
        assert "robot" not in q.lower()
    assert bq.is_llm_context(window)


def test_photo_rejects_robot_for_llm_context_and_text_art():
    ctx = "JetBlue BlueBot knowledge bot LLM"
    assert bq.photo_rejected({"alt": "Orange KUKA industrial robot arm in a factory"}, ctx)
    assert bq.photo_rejected({"alt": "Wooden blocks spelling the word STRATEGY"}, "")
    assert bq.photo_rejected({"alt": "Airline agent at a check-in counter"}, ctx) is None


# ----------------------------------------------------------------- items 1,2,4,5,7,8
def _synthetic_episode():
    filler = "We keep unpacking how the operating model changes day to day work for teams."
    specs = [
        ("Opening", [], {}),
        ("Coca-Cola at Scale",
         [{"type": "stat", "label": "Ad versions", "value": "10,000",
           "subtext": "in 180 countries and 130 languages", "show_at_line": 3, "hide_at_line": 4}],
         {2: "Coca-Cola produced 10,000 versions of an ad in 180 countries and 130 languages."}),
        ("Accenture's Gains",
         [{"type": "stat", "label": "Faster delivery", "value": "25-35%", "show_at_line": 3, "hide_at_line": 4},
          {"type": "stat", "label": "Faster", "value": "25-35%", "show_at_line": 9, "hide_at_line": 10},
          {"type": "bullets", "title": "Operating model",
           "items": ["Sponsor: sets goals", "Builders: ship pilots", "Owners: run it"],
           "show_at_line": 5, "hide_at_line": 18}],
         {2: "Accenture saw work get 25-35% faster.", 8: "Again, 25-35% faster across teams."}),
    ]
    segs = []
    for si, (title, overlays, spoken) in enumerate(specs):
        lines = [{"line_id": f"s{si+1:02d}_l{k+1:02d}", "host_id": "A" if k % 2 == 0 else "B",
                  "text": spoken.get(k, filler)} for k in range(20)]
        visuals = []
        if si == 2:
            visuals = [{"template": "TitleCard", "start_after_line": lines[0]["line_id"],
                        "data": {"title": title, "subtitle": "Why it matters"}}]
        segs.append({"segment_id": f"s{si+1:02d}", "title": title, "lines": lines,
                     "overlays": overlays, "visuals": visuals})
    return segs


def _fake_candidates(self, query, orientation="landscape"):
    base = abs(hash(query)) % 10_000_000
    return [{"id": str(base + i), "url": f"https://example.invalid/{base + i}.jpg",
             "alt": f"people at work {query}", "photographer": "t", "page_url": ""} for i in range(20)]


def test_planner_beats_no_overlap_dedupe_and_broll():
    import visual_queue

    ep = {"topic": "Planner test", "hosts": [{"id": "A"}, {"id": "B"}], "segments": _synthetic_episode()}
    clips = {"clips": [{"line_id": ln["line_id"], "duration_ms": 6000}
                       for s in ep["segments"] for ln in s["lines"]]}
    orig = bq.PexelsPicker.candidates
    bq.PexelsPicker.candidates = _fake_candidates  # offline: no Pexels API
    try:
        with tempfile.TemporaryDirectory() as td:
            m = visual_queue.build_visual_manifest(ep, clips, Path(td), pexels_key="offline-test", audio_path=None)
            report = json.loads((Path(td) / "visual_plan_report.json").read_text(encoding="utf-8"))
    finally:
        bq.PexelsPicker.candidates = orig
    segcards = [v for v in m["visuals"] if v["template"] == "SegmentCard"]
    assert len(segcards) == 3
    all_beats = []
    photo_ids = []
    for v in segcards:
        beats = v["data"].get("beats") or []
        cards = sorted((b for b in beats if b["type"] != "broll"), key=lambda b: b["show_ms"])
        for a, b in zip(cards, cards[1:]):
            assert a["hide_ms"] <= b["show_ms"], (a, b)  # cards never overlap / mask each other
        brolls = sorted((b for b in beats if b["type"] == "broll"), key=lambda b: b["show_ms"])
        for a, b in zip(brolls, brolls[1:]):
            assert a["hide_ms"] <= b["show_ms"]
        for b in brolls:
            assert all(not (c["show_ms"] < b["hide_ms"] and b["show_ms"] < c["hide_ms"]) for c in cards)
            photo_ids.append(b["photo_url"])
        photo_ids += [d["photo_url"] for d in v["data"].get("backdrops") or []]
        all_beats += beats
    assert len(photo_ids) == len(set(photo_ids)), "item 8: no stock photo reused within an episode"
    stats = [b for b in all_beats if b["type"] == "stat"]
    values = [b["value"] for b in stats]
    assert values.count("25-35%") == 1, values  # item 7: one hit per key stat
    ten_k = next(b for b in stats if b["value"].startswith("10,000"))
    assert {c["value"] for c in ten_k.get("companions") or []} >= {"180", "130"}, ten_k
    assert any(b["type"] == "chapter" for b in all_beats), "mid-episode chapter card expected"
    assert any(b["type"] == "diagram" for b in all_beats), "labeled bullets -> diagram expected"
    assert report["summary"]["broll_cuts"] > 0
    assert report["summary"]["broll_pct_of_segments"] >= 30, report["summary"]


# ------------------------------------------------ 2026-09-25 polish B: markdown
def test_strip_markdown_keeps_words():
    from text_clean import strip_markdown
    assert strip_markdown("How do organizations actually *build* the foundation?") == \
        "How do organizations actually build the foundation?"
    assert strip_markdown("This is **really** big, _truly_, with `code`.") == "This is really big, truly, with code."
    assert strip_markdown("***triple*** and dangling *star and bold** end") == "triple and dangling star and bold end"
    assert strip_markdown("snake_case_name stays") == "snake_case_name stays"
    src = "it's *not* about *tools*, it's **people**."
    assert len(strip_markdown(src).split()) == len(src.split())


def test_karaoke_words_have_no_markdown_and_keep_timing():
    import visual_queue

    episode = {"segments": [{"segment_id": "s01", "title": "T", "lines": [
        {"line_id": "l1", "host_id": "A", "text": "How do teams actually *build* the **foundation** for that?"}]}]}
    clips = {"clips": [{"line_id": "l1", "duration_ms": 3000}]}
    heard = [("How", 0, 200), ("do", 200, 300), ("teams", 300, 600), ("actually", 600, 1000),
             ("build", 1000, 1400), ("the", 1400, 1500), ("foundation", 1500, 2200),
             ("for", 2200, 2400), ("that?", 2400, 2900)]
    sys.modules["whisper_align"] = _fake_whisper(heard)
    try:
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "podcast.wav"
            wav.write_bytes(b"RIFF0000WAVE")
            words = visual_queue.compute_word_timings(episode, clips, audio_path=wav)
    finally:
        sys.modules.pop("whisper_align", None)
    shown = [w["word"] for w in words]
    assert not any("*" in w or "_" in w for w in shown), shown
    b = next(w for w in words if w["word"] == "build")
    assert (b["start_ms"], b["end_ms"]) == (1000, 1400), b
    assert episode["segments"][0]["lines"][0]["text"].count("*") == 6  # caller's episode untouched


# ------------------------------------------------ stat value enrichment (M7)
def test_stat_value_restores_currency_and_scale():
    import visual_timeline as vt
    e = vt.enrich_stat_value
    # 2026-09-28 item 6: the spoken qualifier is kept (static render, never counts up)
    assert e("4", "Despite spending over $4 billion, it failed.") == "Over $4 billion"
    assert e("304million", "a staggering $304 million write-down") == "$304 million"
    assert e("90%", "cut deployment time by 90%") == "90%"
    assert e("90%", "cut deployment time by up to 90%.") == "Up to 90%"
    assert e("12", "12 percent of firms") == "12%"
    assert e("Up to 6.9%", "up to 6.9%") == "Up to 6.9%"
    assert e("4", "about 40 firms and 4 teams") == "4"


# ======================================================= 2026-09-28 M7 review
def test_ranges_and_limits_are_static():  # item 6
    import visual_timeline as vt
    for v in ["Up to 6.9%", "up to 90%", "about 40%", "around 3x", "nearly 50%", "over $4 billion",
              "more than 1,000", "~40%", "5-10%", "25–35%", "10,000+", "5 to 10%"]:
        assert vt.stat_is_static(v), v
    for v in ["25%", "42%", "$304 million", "10,000", "6.9%"]:
        assert not vt.stat_is_static(v), v


def test_stat_count_up_lands_before_spoken_and_holds():  # items 1 + 5
    import visual_queue

    filler = "We keep unpacking how the operating model changes day to day work for teams."
    spoken = {3: "Their pilot cut deployment time by up to 90% in the first year.",
              9: "Only 25% of CEOs say they feel fully prepared to deploy it."}
    lines = [{"line_id": f"s01_l{k+1:02d}", "host_id": "AB"[k % 2], "text": spoken.get(k, filler)} for k in range(16)]
    ep = {"topic": "t", "hosts": [{"id": "A"}, {"id": "B"}], "segments": [
        {"segment_id": "s01", "title": "Deployment", "lines": lines, "overlays": [
            {"type": "stat", "label": "Deployment time", "value": "90%", "show_at_line": 4, "hide_at_line": 5},
            {"type": "stat", "label": "CEOs prepared", "value": "25%", "show_at_line": 10, "hide_at_line": 11}]}]}
    clips = {"clips": [{"line_id": ln["line_id"], "duration_ms": 6000} for ln in lines]}
    orig = bq.PexelsPicker.candidates
    bq.PexelsPicker.candidates = _fake_candidates
    try:
        with tempfile.TemporaryDirectory() as td:
            m = visual_queue.build_visual_manifest(ep, clips, Path(td), pexels_key="offline-test", audio_path=None)
    finally:
        bq.PexelsPicker.candidates = orig
    beats = [b for v in m["visuals"] if v["template"] == "SegmentCard" for b in v["data"].get("beats") or []]
    stats = {b["value"]: b for b in beats if b["type"] == "stat"}
    assert "Up to 90%" in stats, list(stats)
    rng, single = stats["Up to 90%"], stats["25%"]
    assert rng["count_up"] is False and rng.get("static"), rng
    for b in (rng, single):
        assert b["show_ms"] <= b["land_ms"] < b["hide_ms"], b
        assert b["hide_ms"] - b["land_ms"] >= 4000, b  # item 5: >= 4 s readable after landing
    if single["count_up"]:
        # StatHero counts over min(1300, land-120) ms -> finished before the word
        assert single["land_ms"] - single["show_ms"] >= 570, single


def test_quadrant_from_script_overlay():  # item 2
    import visual_timeline as vt
    ov = {"type": "quadrant", "title": "Four AI Strategies",
          "x_axis": {"label": "Value-Chain Control"}, "y_axis": {"label": "Technological Breadth"},
          "cells": [{"label": "Focused Differentiation", "x": "low", "y": "low"},
                    {"label": "Vertical Integration", "x": "high", "y": "low"},
                    {"label": "Collaborative Ecosystem", "x": "low", "y": "high"},
                    {"label": "Platform Leadership", "x": "high", "y": "high"}],
          "show_at_line": 2, "hide_at_line": 8}
    q = vt.normalize_quadrant(ov)
    assert q and len(q["cells"]) == 4
    assert {(c["x"], c["y"]) for c in q["cells"]} == {(0, 0), (1, 0), (0, 1), (1, 1)}
    card = vt.quadrant_card(q, title="Four AI Strategies")
    assert card["type"] == "diagram" and card["kind"] == "quadrant"
    assert card["quadrant"]["x_axis"]["label"] == "Value-Chain Control"
    bad = dict(ov, cells=ov["cells"][:3])
    assert vt.normalize_quadrant(bad) is None  # never invent a missing cell


def test_quadrant_not_invented_for_plain_lists():  # items 2 + 10
    import visual_timeline as vt
    segs = _synthetic_episode()
    ep = {"segments": segs}
    assert vt.detect_quadrant(ep, [s["lines"] for s in segs]) is None


def test_key_takeaways_always_pillars():  # item 9
    import visual_timeline as vt
    d = vt.bullets_to_diagram("Key Takeaways", ["Know your quadrant", "Align ambition", "Build the system"],
                              {"pillars", "cycle", "compare"})
    assert d and d["kind"] == "pillars", d


def test_named_case_broll_queries():  # item 4
    name, qs = bq.named_case_queries("Zillow lost $304 million when its home-flipping algorithm misjudged prices.")
    assert name == "zillow" and qs, (name, qs)
    assert any(w in " ".join(qs) for w in ("home", "house", "real estate")), qs
    name, qs = bq.named_case_queries("Pfizer and BioNTech used AI to speed up the vaccine trial.")
    assert name and any(w in " ".join(qs) for w in ("lab", "vaccine", "scientist")), (name, qs)
    for q in qs:
        assert bq.validate_query(q), q
        assert "robot" not in q.lower()
    assert bq.named_case_queries("We keep unpacking how the operating model changes.")[0] is None


def test_karaoke_defaults_locked():  # item 8
    import re
    src = (HERE / "packages/remotion/src/templates/SegmentCard.tsx").read_text(encoding="utf-8")
    peak = float(re.search(r"KARAOKE_SCALE_PEAK\s*=\s*([\d.]+)", src).group(1))
    assert peak > 1.0
    assert "LOCKED DEFAULTS" in src
    assert re.search(r"transform:\s*`scale\(", src), "karaoke grow must use transform scale"
    assert "voice_colors.json" in src
    assert (HERE / "docs/visual-defaults.md").exists()


def test_stathero_respects_land_and_static():  # items 1 + 6 (TSX contract)
    src = (HERE / "packages/remotion/src/components/overlays/StatHero.tsx").read_text(encoding="utf-8")
    assert "landMs" in src and "countWindowMs" in src and "isStaticStat" in src
    ov = (HERE / "packages/remotion/src/components/overlays/StatOverlay.tsx").read_text(encoding="utf-8")
    assert "QUALIFIER_RE" in ov and "up to" in ov


def test_hook_retell_detector():  # item 3
    import script_dedupe as sd
    hook = [{"line_id": "s01_l01", "host_id": "B", "text": "Take General Motors: its AI designed a seat bracket 40% lighter and 20% stronger, but the factory could not build it."},
            {"line_id": "s01_l02", "host_id": "A", "text": "Contrast that with Apple, which used AI to optimize metalenses for Face ID and shipped them."}]
    retell = [{"line_id": "s02_l01", "host_id": "A", "text": "So why do projects fail?"},
              {"line_id": "s02_l02", "host_id": "B", "text": "Take General Motors and the AI seat bracket design."},
              {"line_id": "s02_l03", "host_id": "B", "text": "The design was 40% lighter and 20% stronger, but GM's factory could not build it."},
              {"line_id": "s02_l04", "host_id": "B", "text": "Compare that to Apple optimizing metalenses for Face ID sensors with AI."}]
    brief = [{"line_id": "s02_l01", "host_id": "A", "text": "That GM versus Apple contrast sets up the real question."},
             {"line_id": "s02_l02", "host_id": "B", "text": "The framework has two dimensions: value-chain control and technological breadth."}]
    mk = lambda nxt: {"segments": [{"segment_id": "s01_hook", "lines": hook}, {"segment_id": "s02", "lines": nxt}]}
    assert sd.detect_hook_retell(mk(retell))["heavy"]
    assert not sd.detect_hook_retell(mk(brief))["heavy"]
    ep, rep = sd.dedupe_hook_retell(mk(retell), use_gemini=False, log=lambda s: None)
    assert rep["action"] == "flagged_only" and ep["segments"][1]["lines"] == retell


def test_factcheck_zero_claims_is_warning(monkeypatch=None):  # item 7
    import os
    import factcheck_episode as fc
    import gemini_util as gu
    saved = {k: os.environ.pop(k, None) for k in gu.KEY_ENV_NAMES}
    try:
        ep = {"segments": [{"segment_id": "s1", "lines": [{"line_id": "l1", "text": "It rose 99%."}]}]}
        _, rep = fc.fact_check_episode(ep, "The source says 40 percent. " * 20, use_gemini=True)
        assert rep["summary"]["status"] == "warning_unchecked", rep["summary"]
        assert fc.factcheck_status_line(rep).startswith("WARNING")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_gemini_util_falls_back_on_quota():  # item 7
    import gemini_util as gu

    class Quota(Exception):
        pass

    calls = []

    def fake(key, model, prompt, cfg):
        calls.append((key, model))
        if model == "m1":
            raise Quota("429 ResourceExhausted: quota GenerateRequestsPerDayPerProjectPerModel-FreeTier, retry in 30s")
        return '{"claims": [{"line_id": "l1"}]}'

    out, meta = gu.generate_json("p", models=["m1", "m2"], keys=["k1", "k2"], call=fake,
                                 log=lambda s: None, sleep=lambda s: None)
    assert meta["model"] == "m2" and out["claims"], meta
    assert calls[:2] == [("k1", "m1"), ("k2", "m1")]

    # 404 on one key (retired for that project) must not skip the other keys
    calls.clear()

    def fake404(key, model, prompt, cfg):
        calls.append((key, model))
        if key == "k1":
            raise Quota("404 This model models/m1 is no longer available to new users")
        return '{"claims": [{"line_id": "l1"}]}'

    out, meta = gu.generate_json("p", models=["m1"], keys=["k1", "k2"], call=fake404,
                                 log=lambda s: None, sleep=lambda s: None)
    assert meta["model"] == "m1" and meta["key_index"] == 1, meta


# ------------------------------------------------------- 2026-10-01 fixes
def test_key_takeaways_never_ellipsis():  # item 1
    import visual_timeline as vt
    long = (
        "Match the model's judgment to the decision the retrieval set can actually "
        "support when the note in front of it is thin"
    )
    d = vt.bullets_to_diagram(
        "Key Takeaways",
        [long, "Align ambition with the quadrant you can staff", "Build the system that keeps the call honest"],
        set(),
    )
    assert d and d["kind"] == "pillars", d
    blob = json.dumps(d, ensure_ascii=False)
    assert "…" not in blob and "..." not in blob
    assert d["nodes"][0]["label"] == long
    assert vt._card_duration(
        {"type": "diagram", "kind": "pillars", "title": "Key Takeaways", "nodes": d["nodes"]}
    ) >= 8000
    src = (HERE / "packages/remotion/src/components/overlays/DiagramOverlay.tsx").read_text(encoding="utf-8")
    assert "fitFontPx" in src
    assert "textOverflow" not in src
    assert "lineClamp" not in src


def test_transition_stat_snaps_to_final():  # item 3
    import visual_timeline as vt
    assert vt.transition_final_value("from 16% to 99%") == "99%"
    assert vt.transition_final_value("16% → 99%") == "99%"
    assert vt.transition_final_value("5 to 10%") is None  # range, full string stays
    shown, static = vt.normalize_stat_display("99%", "Accuracy moved from 16% to 99% after the fix.")
    assert shown == "99%" and static, (shown, static)
    shown, static = vt.normalize_stat_display("from 16% to 99%", "Accuracy moved from 16% to 99% after the fix.")
    assert shown == "99%" and static
    # A different figure on the same sentence is not rewritten to the landing value.
    shown, _static = vt.normalize_stat_display("3", "It rose 3 times, from 16% to 99% on the same set.")
    assert shown == "3×", shown
    assert vt.stat_is_static("from 16% to 99%")
    assert vt.stat_is_static("5 to 10%")
    src = (HERE / "packages/remotion/src/components/overlays/StatOverlay.tsx").read_text(encoding="utf-8")
    hero = (HERE / "packages/remotion/src/components/overlays/StatHero.tsx").read_text(encoding="utf-8")
    assert "transitionFinal" in src and "canonicalStatValue" in src
    assert "canonicalStatValue" in hero


def test_multiplier_renders_times_and_bare_number_dropped():  # item 4
    import visual_timeline as vt
    assert vt.apply_multiplier_symbol("3x") == "3×"
    assert vt.apply_multiplier_symbol("3 times") == "3×"
    assert vt.normalize_stat_display("3", "Throughput rose 3 times once the cache landed.")[0] == "3×"
    assert vt.normalize_stat_display("3×", "")[0] == "3×"
    assert vt.normalize_stat_display("7", "There are 7 items on the list and none of them are a rate.")[0] is None
    assert vt.is_bare_unitless("3")
    assert not vt.is_bare_unitless("10,000")
    assert not vt.is_bare_unitless("12%")
    assert not vt.is_bare_unitless("$4 billion")


def test_adjacent_same_value_stats_deduped():  # item 5
    import visual_queue
    import visual_timeline as vt

    cards = [
        {"type": "stat", "value": "99%", "label": "Accuracy", "desired": 1000, "prio": 1},
        {"type": "stat", "value": "99%", "label": "Adoption", "desired": 9000, "prio": 1},
        {"type": "diagram", "title": "Flow", "desired": 20000, "prio": 2},
        {"type": "stat", "value": "99%", "label": "Later accuracy", "desired": 32000, "prio": 1},
    ]
    stats = [c for c in vt.dedupe_adjacent_stat_cards(cards) if c["type"] == "stat"]
    assert [c["label"] for c in stats] == ["Accuracy", "Later accuracy"], stats

    filler = "We keep unpacking how the operating model changes day to day work for teams."
    lines = [{"line_id": f"l{k+1:02d}", "host_id": "AB"[k % 2], "text": filler} for k in range(12)]
    lines[2]["text"] = "Model accuracy reached 99% on the held-out set."
    lines[4]["text"] = "Adoption reached 99% in the pilot group that same week."
    ep = {"topic": "t", "hosts": [{"id": "A"}, {"id": "B"}], "segments": [{
        "segment_id": "s01", "title": "Accuracy", "lines": lines, "overlays": [
            {"type": "stat", "label": "Accuracy", "value": "99%", "show_at_line": 3, "hide_at_line": 4},
            {"type": "stat", "label": "Adoption", "value": "99%", "show_at_line": 5, "hide_at_line": 6},
        ]}]}
    clips = {"clips": [{"line_id": ln["line_id"], "duration_ms": 6000} for ln in lines]}
    orig = bq.PexelsPicker.candidates
    bq.PexelsPicker.candidates = _fake_candidates
    try:
        with tempfile.TemporaryDirectory() as td:
            m = visual_queue.build_visual_manifest(ep, clips, Path(td), pexels_key="offline-test", audio_path=None)
    finally:
        bq.PexelsPicker.candidates = orig
    beats = [b for v in m["visuals"] if v["template"] == "SegmentCard" for b in v["data"].get("beats") or []]
    heroes = [b for b in beats if b["type"] == "stat" and "99" in str(b.get("value"))]
    assert len(heroes) == 1, heroes


def test_chapter_subtitles_are_unique():  # item 7
    import re
    import visual_timeline as vt

    filler = "We keep unpacking how the operating model changes day to day work for teams."

    def seg(i, title, distinctive):
        lines = [{"line_id": f"s{i}_l{k}", "host_id": "A", "text": distinctive if k == 3 else filler} for k in range(8)]
        return {
            "segment_id": f"s{i}", "title": title, "lines": lines,
            "visuals": [{"template": "TitleCard", "data": {"title": title, "subtitle": "Why it matters"}}],
        }

    segs = [
        seg(1, "Opening", "Cold open image of a factory floor at dawn today."),
        seg(2, "Judgment", "Judgment means the call the model makes when the retrieved note is thin."),
        seg(3, "Retrieval", "Retrieval means which passages actually land in the prompt window."),
        seg(4, "Close", "Thanks for listening through the wrap up today."),
    ]
    seg_lines = [[{"idx": k, "text": ln["text"], "line_id": ln["line_id"], "host": "A"}
                  for k, ln in enumerate(s["lines"])] for s in segs]
    durs = [48000, 48000, 48000, 20000]
    ch = vt.pick_chapters({"segments": segs}, seg_lines, durs, sum(durs), [], lambda t: t)
    subs = [c["subtitle"] for c in ch.values()]
    assert len(subs) >= 2, ch
    keys = [re.sub(r"[^a-z0-9]+", " ", s.lower()).strip() for s in subs]
    assert len(keys) == len(set(keys)), subs
    blob = " ".join(subs).lower()
    assert "why it matters" not in blob, subs
    assert "judgment" in blob or "thin" in blob
    assert "retrieval" in blob or "passages" in blob


def test_full_stat_string_stored_on_overlay():  # item 10
    import visual_queue
    import visual_timeline as vt

    assert vt.normalize_stat_display(
        "40%", "More than 40% of teams changed how they work."
    )[0] == "More than 40%"
    filler = "We keep unpacking how the operating model changes day to day work for teams."
    lines = [{"line_id": f"l{k+1:02d}", "host_id": "A", "text": filler} for k in range(8)]
    lines[0]["text"] = "More than 40% of teams changed how they work."
    ep = {"topic": "t", "hosts": [{"id": "A"}, {"id": "B"}], "segments": [{
        "segment_id": "s01", "title": "Retention", "lines": lines, "overlays": [
            {"type": "stat", "label": "Teams", "value": "40%", "show_at_line": 1, "hide_at_line": 2},
        ]}]}
    clips = {"clips": [{"line_id": ln["line_id"], "duration_ms": 6000} for ln in lines]}
    orig = bq.PexelsPicker.candidates
    bq.PexelsPicker.candidates = _fake_candidates
    try:
        with tempfile.TemporaryDirectory() as td:
            m = visual_queue.build_visual_manifest(ep, clips, Path(td), pexels_key="offline-test", audio_path=None)
    finally:
        bq.PexelsPicker.candidates = orig
    beats = [b for v in m["visuals"] if v["template"] == "SegmentCard" for b in v["data"].get("beats") or []]
    stat = next(b for b in beats if b["type"] == "stat")
    assert stat["value"] == "More than 40%", stat


def test_broll_rejects_ghosting_atmosphere_and_slide_labels():  # items 6 + 8
    assert bq.photo_rejected({"alt": "Double exposure portrait of a businessman in the city"}, "budget meeting")
    assert bq.photo_rejected({"alt": "Abstract bokeh lights background"}, "team meeting about retrieval")
    assert bq.photo_rejected(
        {"alt": "Business template with the words PROMOTION and GROWTH"},
        "budget flow diagram",
        role="diagram",
    )
    assert bq.photo_rejected(
        {"alt": "Person standing at a whiteboard template"},
        "draw the diagram",
        role="diagram",
    )
    assert bq.photo_rejected({"alt": "Accountant reviewing a budget spreadsheet"}, "budget meeting") is None
    for q in ("operations manager reviewing documents", "colleagues reviewing printed workflow", "project schedule on a desk"):
        ok, why = bq.validate_query(q)
        assert ok, (q, why)


def test_explain_and_recap_get_a_graphic_without_a_seat():  # item 9
    import visual_timeline as vt
    lines = [
        {"text": "Judgment is the call the model makes when retrieval comes back thin."},
        {"text": "The retrieved note has to be enough to support that call in production."},
    ]
    seg = {"segment_id": "s03", "title": "Judgment", "lines": lines, "overlays": []}
    assert vt.needs_strong_graphic(seg, lines) == "explain"
    card = vt.synthesize_strong_graphic(seg, lines, "explain")
    assert card and card["type"] == "diagram", card
    recap_lines = [
        {"text": "The first lesson is to match the call to the evidence you actually retrieved."},
        {"text": "The second lesson is to measure the miss before you scale the pilot."},
    ]
    recap = {"segment_id": "s09_recap", "title": "Recap", "lines": recap_lines}
    assert vt.needs_strong_graphic(recap, recap_lines) == "recap"
    takeaways = vt.synthesize_strong_graphic(recap, recap_lines, "recap")
    assert takeaways and takeaways["kind"] == "pillars" and "…" not in json.dumps(takeaways, ensure_ascii=False)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {exc!r}")
    print(f"{'OK' if not fails else 'FAILED'} ({fails} failures)")
    raise SystemExit(1 if fails else 0)
