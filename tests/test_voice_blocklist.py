"""Adam (am_adam and any adam variant/blend) must never narrate."""
from __future__ import annotations

import io
import json
import random
import re
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import voice_policy as vp  # noqa: E402


class VoiceBlocklistTests(unittest.TestCase):
    def test_config_blocks_adam(self):
        cfg = json.loads((ROOT / "config" / "voice_blocklist.json").read_text(encoding="utf-8"))
        self.assertIn("am_adam", cfg["kokoro"]["blocked"])
        for v in ("am_adam", "AM_ADAM", "bm_adam", "am_adam(0.5)+af_bella(0.5)", "af_bella+am_adam"):
            self.assertTrue(vp.is_blocked(v), v)
        for v in ("am_michael", "bm_george", "af_bella", "bf_emma", "af_sarah", ""):
            self.assertFalse(vp.is_blocked(v), v)

    def test_explicit_request_swaps_same_gender_accent_with_warning(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.assertEqual(vp.enforce_voice("am_adam", context="test"), "am_michael")
            self.assertEqual(vp.enforce_voice("bm_adam"), "bm_george")
            self.assertEqual(vp.enforce_voice("am_adam+af_bella"), "am_michael+af_bella")
        self.assertIn("blocklisted", buf.getvalue())
        self.assertEqual(vp.enforce_voice("af_bella"), "af_bella")

    def test_episode_hosts_are_patched(self):
        ep = {"hosts": [{"id": "A", "voice_id": "bm_george"}, {"id": "B", "voice_id": "am_adam"}]}
        with redirect_stderr(io.StringIO()):
            changes = vp.enforce_episode_hosts(ep)
        self.assertEqual(changes, [("am_adam", "am_michael")])
        self.assertEqual(ep["hosts"][1]["voice_id"], "am_michael")

    def test_pools_and_random_rotation_never_pick_adam(self):
        import research_podcast as rp
        self.assertFalse(any(vp.is_blocked(v) for v in rp.MALE_VOICES + rp.FEMALE_VOICES))
        for seed in range(500):
            a, b = rp._pick_voices(random.Random(seed))
            self.assertFalse(vp.is_blocked(a) or vp.is_blocked(b), (seed, a, b))
            self.assertNotEqual(a, b)

    def test_ui_label_lists_are_filtered(self):
        labels = ["bm_george (Male)", "am_adam (Male)", "af_bella (Female)"]
        self.assertEqual(vp.filter_voices(labels), ["bm_george (Male)", "af_bella (Female)"])

    def test_no_adam_defaults_left_in_repo_sources(self):
        allowed = {"config/voice_blocklist.json", "voice_policy.py",
                   "packages/mcp-server/src/kokoro/voicePolicy.ts", "tests/test_voice_blocklist.py",
                   "README.md"}
        pat = re.compile(r"\bam_adam\b")
        hits = []
        for p in ROOT.rglob("*"):
            rel = p.relative_to(ROOT).as_posix()
            if (not p.is_file() or rel in allowed or p.suffix not in {".py", ".ts", ".tsx", ".json", ".md"}
                    or re.match(r"(output|node_modules|\.venv|dist|reviews)/", rel) or "/node_modules/" in rel):
                continue
            try:
                if pat.search(p.read_text(encoding="utf-8", errors="ignore")):
                    hits.append(rel)
            except OSError:
                pass
        self.assertEqual(hits, [])

    def test_typescript_policy_uses_same_config(self):
        ts = (ROOT / "packages/mcp-server/src/kokoro/voicePolicy.ts").read_text(encoding="utf-8")
        self.assertIn("voice_blocklist.json", ts)
        client = (ROOT / "packages/mcp-server/src/kokoro/kokoroClient.ts").read_text(encoding="utf-8")
        self.assertIn("enforceVoice(voiceId", client)


if __name__ == "__main__":
    unittest.main()
