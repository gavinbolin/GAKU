"""Every GAKU upload must list real, deduplicated sources within YouTube limits."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import episode_sources as es  # noqa: E402

RESULTS_MD = """# GAKU - Learning Resources

## How 3D Model Animation Works

- **[glTF 2.0 Specification [Khronos]](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html)** - _Article_
  - Skeletal animation, skins and morph targets in glTF.
- **[Blender Manual: Armatures](https://docs.blender.org/manual/en/latest/animation/armatures/)** - _Article_
  - Bones, rigging and skinning in Blender.
- **[Blender Manual: Armatures](http://www.docs.blender.org/manual/en/latest/animation/armatures)** - _Article_
  - duplicate with different scheme/host/slash
- **[Executive, Human Resources : Square Textiles](https://bdjobs.com/h/details/1)** - _Article_
  - Textiles jobs in Bangladesh.
"""
EPISODE = {"topic": "How 3D Model Animation Works",
           "segments": [{"title": "Bones, Hierarchies, and Matrices"}, {"title": "Skinning and Morph Targets"}]}


def _run_dir(tmp: str, results: str | None = RESULTS_MD) -> Path:
    d = Path(tmp)
    if results is not None:
        (d / "results.md").write_text(results, encoding="utf-8")
    return d


class CaptureTests(unittest.TestCase):
    def test_parses_real_links_dedupes_and_flags_offtopic(self):
        with tempfile.TemporaryDirectory() as tmp:
            srcs = es.capture_sources(_run_dir(tmp), EPISODE)
        urls = [s["url"] for s in srcs]
        self.assertEqual(len(urls), 3)  # duplicate armature link removed
        self.assertIn("https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html", urls)
        self.assertEqual(srcs[0]["title"], "glTF 2.0 Specification [Khronos]")
        listed = [s["url"] for s in es.listed_sources(srcs)]
        self.assertNotIn("https://bdjobs.com/h/details/1", listed)  # off-topic scrape result
        self.assertEqual(len(listed), 2)

    def test_document_source_title_without_inventing_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _run_dir(tmp, None)
            (d / "hbr_source.md").write_text("# Source: Match Your AI Strategy.pdf\n\nHarvard Business Review\n",
                                             encoding="utf-8")
            srcs = es.capture_sources(d, {"topic": "AI strategy"})
        self.assertEqual(srcs, [{"title": "Match Your AI Strategy (Harvard Business Review)", "url": None,
                                 "kind": "document", "origin": "hbr_source.md", "relevant": True}])

    def test_sources_json_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _run_dir(tmp)
            es.write_sources_json(d, es.capture_sources(d, EPISODE))
            (d / "results.md").unlink()
            again = es.capture_sources(d, EPISODE)
        self.assertEqual(len(es.listed_sources(again)), 2)


class DescriptionTests(unittest.TestCase):
    def test_appends_sources_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            desc, _ = es.build_description("Intro text", _run_dir(tmp), EPISODE)
        self.assertTrue(desc.startswith("Intro text\n\nSources:\n- glTF 2.0 Specification [Khronos]: https://"))
        self.assertEqual(desc.count("docs.blender.org"), 1)
        self.assertNotIn("bdjobs", desc)

    def test_long_list_is_trimmed_by_whole_lines_under_limit(self):
        srcs = [{"title": f"Reference number {i} " + "x" * 80, "url": f"https://example.org/{i}/" + "p" * 60}
                for i in range(200)]
        out = es.append_sources("Body " * 300, srcs, reserve=80)
        self.assertLessEqual(len(out), es.YOUTUBE_DESCRIPTION_LIMIT - 80)
        self.assertRegex(out, r"\(\+\d+ more sources not shown\)$")
        for line in out.split("Sources:\n", 1)[1].splitlines()[:-1]:
            self.assertRegex(line, r"https://example\.org/\d+/p{60}$")  # URLs never cut

    def test_rerun_replaces_existing_block(self):
        srcs = [{"title": "A", "url": "https://a.example/"}]
        once = es.append_sources("Body", srcs)
        self.assertEqual(es.append_sources(once, srcs), once)

    def test_angle_brackets_stripped(self):
        out = es.append_sources("Body", es.dedupe([{"title": "<b>Bold</b> docs", "url": "https://a.example/"}]))
        self.assertNotIn("<", out)


class RefusalTests(unittest.TestCase):
    def test_no_sources_refuses_unless_overridden(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"GAKU_ALLOW_NO_SOURCES": ""}):
            with self.assertRaises(es.MissingSourcesError):
                es.build_description("Body", _run_dir(tmp, None), EPISODE)
            desc, _ = es.build_description("Body", _run_dir(tmp, None), EPISODE, allow_no_sources=True)
            self.assertEqual(desc, "Body")
            with mock.patch.dict(os.environ, {"GAKU_ALLOW_NO_SOURCES": "1"}):
                es.build_description("Body", _run_dir(tmp, None), EPISODE)

    def test_only_offtopic_sources_still_refuses(self):
        md = "- **[Clio](https://app.clio.com/)** - _Article_\n  - Clio\n"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"GAKU_ALLOW_NO_SOURCES": ""}):
            with self.assertRaises(es.MissingSourcesError):
                es.build_description("Body", _run_dir(tmp, md), EPISODE)


@unittest.skipUnless((ROOT.parent / "youtube_upload.py").exists(), "shared uploader not present")
class UploaderTests(unittest.TestCase):
    def setUp(self):
        import youtube_upload
        self.yt = youtube_upload

    def test_uploader_refuses_before_any_api_call(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"GAKU_ALLOW_NO_SOURCES": ""}), \
                mock.patch.object(self.yt, "get_authenticated_service") as auth:
            with self.assertRaises(es.MissingSourcesError):
                self.yt.upload_video(str(Path(tmp) / "x.mp4"), "T", "Body", episode_dir=tmp, episode=EPISODE)
            auth.assert_not_called()

    def test_uploader_sends_sources_and_disclosure(self):
        sent = {}

        class _Req:
            def next_chunk(self):
                return None, {"id": "vid123"}

        fake = mock.MagicMock()
        fake.videos.return_value.insert.side_effect = lambda **kw: sent.update(kw) or _Req()
        with tempfile.TemporaryDirectory() as tmp:
            d = _run_dir(tmp)
            mp4 = d / "x.mp4"
            mp4.write_bytes(b"0" * 10)
            with mock.patch.object(self.yt, "get_authenticated_service", return_value=fake), \
                    mock.patch.object(self.yt, "MediaFileUpload"):
                self.yt.upload_video(str(mp4), "T", "Body", episode_dir=str(d), episode=EPISODE,
                                     set_thumbnail=False)
        desc = sent["body"]["snippet"]["description"]
        self.assertIn("\nSources:\n- glTF 2.0 Specification", desc)
        self.assertTrue(desc.endswith(self.yt.SYNTHETIC_DISCLOSURE))
        self.assertLessEqual(len(desc), 5000)


if __name__ == "__main__":
    unittest.main()
