"""Research search: short queries, relevance filter, sources feed notes + sources.json."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import episode_sources as es  # noqa: E402
import research_search as rs  # noqa: E402

TOPIC_3D = "How 3D Model Animation Works: A Technical, Beginner-Friendly Deep Dive"
S = rs.Source

# What the old gaku.py queries actually returned for TOPIC_3D (results.md, 2026-11-11).
OLD_JUNK = [
    S("Best Evidence Science Teaching (age 11 to 16) - STEM Learning", "https://www.stem.org.uk/x",
      "A large collection of free resources for secondary school science."),
    S("The Best Resources to Learn AI For Free in 2026 - DataCamp", "https://www.datacamp.com/blog/x",
      "Discover the best free resources to learn AI"),
    S("Executive, Human Resources : Square Textiles PLC. || Bdjobs.com", "https://bdjobs.com/h/details/1",
      "textiles conglomerates in Bangladesh"),
    S("Clio", "https://app.clio.com/", "Clio"),
    S("Unreal Engine 5.9 Documentation - Epic Dev", "https://dev.epicgames.com/x",
      "Complete resources for learning to use Unreal Engine 5"),
]
GOOD = [
    S("What Is 3D Animation? Process & Techniques | VFS Blog", "https://vfs.edu/news/what-is-3D-animation",
      "3D animation brings models to life through rigging and keyframes"),
    S("How Does 3D Animation Work? Beginner's Guide", "https://www.hyphemotion.com/how-does-3d-animation-work/",
      "modeling, rigging, animation and rendering"),
    S("3D Animation: Understanding Modeling, Rigging, and Rendering", "https://www.perfectformdesign.com/3d",
      "how a 3D model is animated"),
]


class QueryTests(unittest.TestCase):
    def test_core_phrase_strips_subtitle_and_filler(self):
        self.assertEqual(rs.core_phrase(TOPIC_3D), "3D Model Animation")
        self.assertEqual(rs.core_phrase("How Vector Databases Work: Embeddings and Search"), "Vector Databases")
        self.assertEqual(rs.core_phrase("Quantum Computing"), "Quantum Computing")

    def test_queries_are_short_never_the_quoted_title(self):
        qs = rs.build_queries(TOPIC_3D)
        self.assertIn("3D Model Animation", qs)
        self.assertFalse(any('"' in q or "Deep Dive" in q or "Beginner-Friendly" in q for q in qs), qs)
        self.assertTrue(all(len(q.split()) <= 7 for q in qs), qs)

    def test_subtitle_parts_and_segments_become_facets(self):
        qs = rs.build_queries("How Vector Databases Work: Embeddings, Indexes, and Similarity Search",
                              segment_titles=["Hook", "HNSW Graphs", "Key Takeaways"])
        self.assertIn("Vector Databases Embeddings", qs)
        self.assertIn("Vector Databases Similarity Search", qs)
        self.assertIn("Vector Databases HNSW Graphs", qs)
        self.assertFalse(any("Takeaways" in q or q.endswith("Hook") for q in qs))


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.core = es._tokens("3D Model Animation")
        self.facets = es._tokens("Computer animation 3D modeling Skeletal animation") - self.core

    def test_old_offtopic_results_are_dropped_good_kept(self):
        kept, dropped = rs.filter_rank(OLD_JUNK + GOOD, self.core, self.facets)
        self.assertEqual({s.url for s in kept}, {s.url for s in GOOD})
        self.assertEqual(len(dropped), len(OLD_JUNK))

    def test_storefronts_mirrors_and_tool_pages_dropped(self):
        cands = [S("Animation 3D Models - Free & Premium Downloads | CGTrader", "https://www.cgtrader.com/m",
                   "3d animation models"),
                 S("Computer animation - HandWiki", "https://handwiki.org/wiki/Computer_animation",
                   "3D computer animation"),
                 S("AnimateMesh - Rig & Animate Any 3D Model", "https://animatemesh.com/", "3D model animation")]
        kept, _ = rs.filter_rank(cands, self.core, self.facets)
        self.assertEqual(kept, [])

    def test_dedupe_encoded_urls_and_domain_cap(self):
        wiki = [S("Blender (software) - Wikipedia", "https://en.wikipedia.org/wiki/Blender_%28software%29",
                  "3D computer graphics animation model", kind="encyclopedia"),
                S("Blender (software)", "https://en.wikipedia.org/wiki/Blender_(software)",
                  "3D animation model", kind="encyclopedia")]
        same = [S(f"What is 3D animation part {i}", f"https://pixune.com/blog/{i}", "3D animation model guide")
                for i in range(4)]
        kept, _ = rs.filter_rank(wiki + same, self.core, self.facets)
        self.assertEqual(sum("wikipedia" in s.url for s in kept), 1)
        self.assertEqual(sum("pixune" in s.url for s in kept), 2)


class PipelineTests(unittest.TestCase):
    def _fake_research(self):
        wiki_hits = [S("Computer animation - Wikipedia", "https://en.wikipedia.org/wiki/Computer_animation",
                       "art of creating moving images; 3D animation of models", kind="encyclopedia")]
        return rs.research(TOPIC_3D, web=lambda q, k: OLD_JUNK + GOOD, wiki=lambda q, k: wiki_hits,
                           extracts=lambda titles: {"Computer animation": "Computer animation is the process..."},
                           pause=0)

    def test_sources_feed_both_notes_and_sources_json(self):
        res = self._fake_research()
        with tempfile.TemporaryDirectory() as tmp:
            md, sj = rs.write_outputs(res, Path(tmp) / "results.md")
            notes = md.read_text(encoding="utf-8")
            listed = es.listed_sources(es.capture_sources(Path(tmp), {"topic": TOPIC_3D}))
            self.assertTrue(json.loads(sj.read_text(encoding="utf-8"))["sources"])
        want = {s.url for s in res["sources"]}
        self.assertEqual({s["url"] for s in listed}, want)  # same list goes to YouTube...
        for url in want:
            self.assertIn(url, notes)  # ...and into Gemini's RESEARCH_NOTES
        self.assertIn("Computer animation is the process", notes)
        self.assertNotIn("bdjobs", notes)
        self.assertGreaterEqual(len(want), 4)

    def test_run_gaku_uses_research_search(self):
        import research_podcast as rp
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(rp, "DEFAULT_RESULTS", Path(tmp) / "results.md"), \
                mock.patch.object(rs, "research", return_value=self._fake_research()), \
                mock.patch.object(rp.subprocess, "run") as legacy:
            out = rp.run_gaku(TOPIC_3D)
            self.assertTrue((Path(tmp) / "research_sources.json").exists())
            self.assertIn("vfs.edu", out.read_text(encoding="utf-8"))
        legacy.assert_not_called()

    def test_hand_written_source_line_counts_for_old_publish_script(self):
        self.assertTrue(es.description_cites_sources("Intro\n\nSource: Anand & Wu, HBR, Nov 2025.\n"))
        self.assertFalse(es.description_cites_sources("Intro only"))


if __name__ == "__main__":
    unittest.main()
