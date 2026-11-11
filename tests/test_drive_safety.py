"""Offline tests: never touch a real Drive mount."""
import ast
import contextlib
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

SOURCE = Path(__file__).resolve().parents[1] / "research_podcast.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
NODE = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_copy_to_drive")
NS = {"Path": Path, "os": os, "shutil": shutil, "sys": sys}
exec(compile(ast.Module(body=[NODE], type_ignores=[]), str(SOURCE), "exec"), NS)

class DriveSafetyTests(unittest.TestCase):
    def test_disabled_never_checks_target_or_copies(self):
        target = Mock()
        NS["DRIVE_SYNC_DIR"] = target
        with patch.object(shutil, "copyfile") as copy:
            NS["_copy_to_drive"](Path("local.mp4"), "run", disabled=True)
        target.is_dir.assert_not_called()
        copy.assert_not_called()

    def test_environment_disables_copy(self):
        with patch.dict(os.environ, {"GAKU_NO_DRIVE": "true"}), patch.object(shutil, "copyfile") as copy:
            NS["_copy_to_drive"](Path("local.mp4"), "run")
        copy.assert_not_called()

    def test_missing_target_is_not_created(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"GAKU_NO_DRIVE": "0"}):
            target = Path(d) / "missing"
            NS["DRIVE_SYNC_DIR"] = target
            warning = io.StringIO()
            with contextlib.redirect_stderr(warning), patch.object(shutil, "copyfile") as copy:
                NS["_copy_to_drive"](Path("local.mp4"), "run")
            self.assertFalse(target.exists())
            self.assertIn("WARNING", warning.getvalue())
            copy.assert_not_called()

    def test_no_upload_and_no_drive_are_wired(self):
        call = next(n for n in ast.walk(TREE) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_copy_to_drive")
        disabled = next(k.value for k in call.keywords if k.arg == "disabled")
        self.assertIsInstance(disabled, ast.BoolOp)
        self.assertIsInstance(disabled.op, ast.Or)
        self.assertEqual({n.attr for n in disabled.values}, {"no_upload", "no_drive"})

if __name__ == "__main__":
    unittest.main()
