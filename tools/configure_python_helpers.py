"""Wire non-repository launch helpers to the project's single Python venv.
Idempotent; backs up originals once. Run with --apply to install changes.
"""
from pathlib import Path
import argparse
import shutil

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
PYTHON = REPO / ".venv/Scripts/python.exe"

def write(name, old, new, apply):
    if old == new:
        return
    print(f"update: {PARENT / name}")
    if apply:
        dest = PARENT / name
        backup = REPO / "output/takeover_logs/python_parent_before" / name
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dest, backup)
        dest.write_text(new, encoding="utf-8")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for name in ("tools/m5_gaku_rewatch.py", "tools/_m5_most_ai_regen_runner.py", "tools/m5_most_ai_launch.ps1", "tools/m7_match_ai_launch.ps1", "build_quickbooks_video.bat"):
        p = PARENT / name
        old = p.read_text(encoding="utf-8-sig")
        new = old.replace(str(Path.home() / "AppData/Local/Programs/Python/Python310/python.exe"), str(PYTHON))
        new = new.replace("python podcast-pipeline", '"' + str(PYTHON) + '" podcast-pipeline')
        write(name, old, new, args.apply)
    # app.py's child commands use sys.executable, so bootstrap before importing Gradio.
    for name in ("app.py", "gaku.py"):
        p = PARENT / name
        old = p.read_text(encoding="utf-8-sig")
        marker = "# GAKU_PROJECT_VENV_BOOTSTRAP"
        if marker in old:
            continue
        bootstrap = (marker + "\nimport os as _gaku_os\nimport sys as _gaku_sys\n"
            + "_gaku_python = Path(__file__).resolve().parent / 'podcast-pipeline/.venv/Scripts/python.exe'\n"
            + "if Path(_gaku_sys.executable).resolve() != _gaku_python.resolve():\n"
            + "    if not _gaku_python.is_file():\n"
            + "        raise RuntimeError('Create podcast-pipeline/.venv before launching GAKU')\n"
            + "    _gaku_os.execv(str(_gaku_python), [str(_gaku_python), __file__, *_gaku_sys.argv[1:]])\n")
        new = old.replace("from pathlib import Path\n", "from pathlib import Path\n\n" + bootstrap + "\n", 1)
        write(name, old, new, args.apply)
    p = PARENT / "requirements.txt"
    old = p.read_text(encoding="utf-8-sig")
    write("requirements.txt", old, "# Canonical pinned environment lives in the Git checkout.\n-r podcast-pipeline/requirements.txt\n", args.apply)

if __name__ == "__main__":
    main()
