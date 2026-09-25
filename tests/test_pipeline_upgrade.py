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
    assert e("4", "Despite spending over $4 billion, it failed.") == "$4 billion"
    assert e("304million", "a staggering $304 million write-down") == "$304 million"
    assert e("90%", "cut deployment time by 90%") == "90%"
    assert e("12", "12 percent of firms") == "12%"
    assert e("Up to 6.9%", "up to 6.9%") == "Up to 6.9%"
    assert e("4", "about 40 firms and 4 teams") == "4"


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
