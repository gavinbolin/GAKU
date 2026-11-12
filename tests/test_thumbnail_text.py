"""Offline thumbnail text tests: headline heuristic, word-safe fitting, safe areas."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import thumbnail_hybrid as th  # noqa: E402
from PIL import Image  # noqa: E402

HAVE_FONT = th._font_path() is not None
LONG_TITLES = [
    "How 3D Model Animation Works: A Technical, Beginner-Friendly Deep Dive",
    "Match Your AI Strategy to Your Organization's Reality",
    "Most AI Initiatives Fail. This 5-Part Framework Can Help",
    "LangChain how it works in depth and why it matters for security",
    "The Complete Definitive Ultimate Guide to Understanding Everything About Quaternion Interpolation in Modern Game Engines",
    "Supercalifragilisticexpialidocious Antidisestablishmentarianism Pneumonoultramicroscopicsilicovolcanoconiosis",
    "AI",
]


def intersects(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class HeadlineTests(unittest.TestCase):
    def test_subtitle_and_filler_removed(self):
        self.assertEqual(th.headline_from_title(LONG_TITLES[0]), "How 3D Model Animation Works")
        self.assertEqual(th.headline_from_title(LONG_TITLES[1]), "Match Your AI Strategy")
        self.assertEqual(th.headline_from_title(LONG_TITLES[2]), "Most AI Initiatives Fail")

    def test_headline_is_whole_words_and_capped(self):
        for title in LONG_TITLES:
            head = th.headline_from_title(title)
            words = head.split()
            self.assertLessEqual(len(words), th.MAX_HEADLINE_WORDS)
            source = title.replace(",", " ").replace(".", " ").replace(":", " ").split()
            for w in words:
                self.assertIn(w, source, f"{w!r} is not a whole source word of {title!r}")
            self.assertNotIn(words[-1].lower(), {"to", "of", "for", "the", "your"})

    def test_episode_field_precedence_and_accent_markers(self):
        ep = {"topic": LONG_TITLES[0], "thumbnail_text": "How 3D Animation *Works*", "thumbnail_hook": "Old"}
        self.assertEqual(th.resolve_hook_text(ep), "How 3D Animation *Works*")
        self.assertEqual(th.resolve_hook_text(ep, hook="Explicit Hook"), "Explicit Hook")
        words, accents = th.parse_headline("How 3D Animation *Works*")
        self.assertEqual(words, ["HOW", "3D", "ANIMATION", "WORKS"])
        self.assertEqual(accents, {3})
        self.assertEqual(th.resolve_hook_text({"topic": LONG_TITLES[1]}), "Match Your AI Strategy")

    def test_default_accent_is_last_content_word(self):
        self.assertEqual(th.parse_headline("Match Your AI Strategy")[1], {3})


@unittest.skipUnless(HAVE_FONT, "needs a TrueType font")
class FitTests(unittest.TestCase):
    def check_fit(self, words, box, **kw):
        fitted = th.fit_text(words, box, **kw)
        flat = [w for line in fitted.lines for w in line]
        self.assertEqual(flat, words, "fitter must never drop, reorder or cut words")
        self.assertLessEqual(len(fitted.lines), kw.get("max_lines", 3))
        for b in fitted.boxes:
            self.assertGreaterEqual(b[0], box[0]); self.assertGreaterEqual(b[1], box[1])
            self.assertLessEqual(b[2], box[2]); self.assertLessEqual(b[3], box[3])
        return fitted

    def test_long_raw_titles_never_overflow(self):
        for title in LONG_TITLES:
            words = title.upper().split()
            self.check_fit(words, th.LAYOUT_BOXES["bold"]["text"], max_lines=3)

    def test_single_giant_word_shrinks_instead_of_breaking(self):
        fitted = self.check_fit(["PNEUMONOULTRAMICROSCOPICSILICOVOLCANOCONIOSIS"], (56, 118, 770, 612))
        self.assertEqual(len(fitted.lines), 1)

    def test_short_headline_is_big(self):
        fitted = self.check_fit(["HOW", "3D", "ANIMATION", "WORKS"], th.LAYOUT_BOXES["bold"]["text"])
        self.assertGreaterEqual(fitted.size, 120)


@unittest.skipUnless(HAVE_FONT, "needs a TrueType font")
class LayoutTests(unittest.TestCase):
    def test_every_layout_keeps_text_on_canvas_and_off_timestamp(self):
        plate = Image.linear_gradient("L").resize((th.W, th.H)).convert("RGB")
        for layout in th.LAYOUTS:
            for title in LONG_TITLES:
                for head in (title, th.headline_from_title(title)):
                    img, info = th.compose(plate, head, layout, motif=True)
                    self.assertEqual(img.size, (1280, 720))
                    words, _ = th.parse_headline(head)
                    self.assertEqual([w for line in info["lines"] for w in line], words)
                    for b in info["text_boxes"] + [info["badge"]] + ([info["accent_bar"]] if "accent_bar" in info else []):
                        self.assertGreaterEqual(min(b[0], b[1]), 0)
                        self.assertLessEqual(b[2], th.W); self.assertLessEqual(b[3], th.H)
                        self.assertFalse(intersects(b, th.TIMESTAMP_ZONE), (layout, head, b))

    def test_saved_png_is_youtube_sized(self):
        plate = Image.new("RGB", (1920, 1080), (40, 90, 160))
        img, _ = th.compose(plate, "How 3D Animation *Works*", "bold", motif=True)
        with tempfile.TemporaryDirectory() as d:
            path = th.save_youtube_png(img, Path(d) / "t.png")
            self.assertLess(path.stat().st_size, th.MAX_PNG_BYTES)
            with Image.open(path) as saved:
                self.assertEqual(saved.size, (1280, 720))


if __name__ == "__main__":
    unittest.main()
