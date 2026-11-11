import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("review_loop", Path(__file__).resolve().parents[1] / "tools/review_loop.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)

class ReviewTests(unittest.TestCase):
    def test_wer_exact_and_substitution(self):
        self.assertEqual(review.word_error_rate(["a", "b"], ["a", "b"]), 0)
        self.assertEqual(review.word_error_rate(["a", "b"], ["a", "c"]), .5)
        self.assertIsNone(review.word_error_rate([], []))

    def test_caption_sync_global_offset(self):
        captions = [{"word": "hello", "start_ms": 300100}, {"word": "world", "start_ms": 301100}]
        asr = [{"word": "hello", "start_ms": 140}, {"word": "world", "start_ms": 1130}]
        metrics = review.sync_metrics(captions, asr, 300)
        self.assertEqual(metrics["matched_words"], 2)
        self.assertEqual(metrics["median_absolute_ms"], 35)
        self.assertEqual(metrics["p95_absolute_ms"], 40)

    def test_expected_text_is_from_episode_not_captions(self):
        e = {"segments": [{"segment_id": "s1", "lines": [{"line_id": "l1", "text": "First second third fourth", "pause_after_ms": 0}]}]}
        m = {"segment_timings": [{"segment_id": "s1", "start_ms": 0}]}
        d = {"clips": [{"line_id": "l1", "duration_ms": 4000}]}
        words, lines = review.expected_script(e,m,d,1,3)
        self.assertEqual(words, ["second", "third"])
        self.assertEqual(lines[0]["line_id"], "l1")

    def test_caption_selection(self):
        m={"visuals":[{"start_ms":300000,"data":{"sentence_timings":[{"words":[{"word":"Example!","start_ms":100,"end_ms":200}]}]}}]}
        self.assertEqual(review.caption_words(m,300,301)[0]["word"],"example")

if __name__ == "__main__":
    unittest.main()
