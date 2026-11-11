"""Offline regression: registration must not drop synthesis preferences."""
from pathlib import Path
import unittest

class SynthesisTests(unittest.TestCase):
    def test_schema_and_handler_preferences(self):
        source = (Path(__file__).resolve().parents[1] / "packages/mcp-server/src/server.ts").read_text(encoding="utf-8")
        block = source.split('name: "synthesize_research_to_script"', 1)[1].split('name: "gemini_image_query"', 1)[0]
        for name in ("tone", "audience_level", "focus"):
            self.assertIn(name + ': { type: "string" }', block)
            self.assertIn(name + ': args.' + name + ' as string | undefined', block)

if __name__ == "__main__":
    unittest.main()
