"""One-time, idempotent migration of helpers outside this Git checkout.
Backups go to ignored output/takeover_logs/parent_before. No Drive writes.
"""
from pathlib import Path
import argparse
import shutil

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
FILES = ["build_quickbooks_video.bat", "tools/m5_apply_improvements.py", "tools/m5_gaku_rewatch.py", "tools/m5_most_ai_launch.ps1", "tools/m7_match_ai_launch.ps1", "tools/_m5_most_ai_regen_runner.py"]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for name in FILES:
        p = PARENT / name
        if not p.is_file():
            print(f"missing: {p}")
            continue
        old = p.read_text(encoding="utf-8-sig")
        new = old.replace("..PROJECT", ".PROJECT")
        if new == old:
            continue
        print(f"update: {p}")
        if args.apply:
            backup = REPO / "output/takeover_logs/parent_before" / name
            if not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, backup)
            p.write_text(new, encoding="utf-8")

if __name__ == "__main__":
    main()
