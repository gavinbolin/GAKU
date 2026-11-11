import json
from pathlib import Path
import subprocess
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemini_util import gemini_models

class SharedModelsTests(unittest.TestCase):
    def test_python_node_agree(self):
        root = Path(__file__).resolve().parents[1]
        code = "import {geminiModels} from './packages/mcp-server/src/gemini_models.ts'; console.log(JSON.stringify(geminiModels()));"
        node = subprocess.check_output(["node", "--import", "./packages/mcp-server/node_modules/tsx/dist/loader.mjs", "--input-type=module", "-e", code], cwd=root, text=True)
        self.assertEqual(gemini_models(), json.loads(node))

if __name__ == "__main__":
    unittest.main()
