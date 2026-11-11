import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemini_util import generate_json
from gemini_safety import stop_on_gemini_budget_error

class BudgetStopTests(unittest.TestCase):
    def test_quota_stops_before_any_retry_or_rotation(self):
        calls = []
        def fail(*args):
            calls.append(args)
            raise RuntimeError("429 resource exhausted quota")
        with patch.dict(os.environ, {"GAKU_GEMINI_STOP_ON_QUOTA":"1", "GAKU_GEMINI_STOP_FILE":""}):
            with self.assertRaises(SystemExit) as stopped:
                generate_json("test",keys=["fake-1","fake-2"],models=["fake-a","fake-b"],call=fail, sleep=lambda _:self.fail("must not retry"))
        self.assertEqual(stopped.exception.code,85)
        self.assertEqual(len(calls),1)

    def test_billing_and_paid_signals_stop(self):
        with patch.dict(os.environ, {"GAKU_GEMINI_STOP_ON_QUOTA":"true", "GAKU_GEMINI_STOP_FILE":""}):
            for text in ("Enable billing", "Payment required", "paid-tier model", "request would incur charges"):
                with self.assertRaises(SystemExit):
                    stop_on_gemini_budget_error(RuntimeError(text))
            stop_on_gemini_budget_error(RuntimeError("404 model not found"))

    def test_policy_off_preserves_legacy_behavior(self):
        with patch.dict(os.environ, {"GAKU_GEMINI_STOP_ON_QUOTA":"0"}):
            stop_on_gemini_budget_error(RuntimeError("quota"))

    def test_node_policy_exits_and_writes_safe_marker(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d:
            marker=Path(d)/"stop.json"
            code="import {stopOnGeminiBudgetError} from './packages/mcp-server/src/gemini_safety.ts'; stopOnGeminiBudgetError({status:429,message:'quota'}); process.exit(99);"
            env=dict(os.environ,GAKU_GEMINI_STOP_ON_QUOTA="1",GAKU_GEMINI_STOP_FILE=str(marker))
            result=subprocess.run(["node","--import","./packages/mcp-server/node_modules/tsx/dist/loader.mjs","--input-type=module","-e",code],cwd=root,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,85,result.stderr)
            self.assertTrue(json.loads(marker.read_text())["stopped"])

if __name__ == "__main__":
    unittest.main()
