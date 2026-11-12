"""Render local thumbnail review samples. Never touches thumbnail.png or YouTube.

python tools/render_thumbnail_samples.py --episode-dir <run> [--headline "How 3D Animation *Works*"]
Writes <run>/reviews/thumbnails/: <layout>.png (1280x720), <layout>_320.png previews,
old_thumbnail*.png for comparison, contact_sheet.png and samples.json.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import thumbnail_hybrid as th  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--episode-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--headline", help="Thumbnail text override (use *word* for the accent)")
    ap.add_argument("--layouts", default=",".join(th.LAYOUTS))
    args = ap.parse_args()
    ep = args.episode_dir.resolve()
    out = (args.out_dir or ep / "reviews" / "thumbnails").resolve()
    out.mkdir(parents=True, exist_ok=True)
    episode = json.loads((ep / "episode.json").read_text(encoding="utf-8"))
    background = th.select_background(ep, episode)
    samples, previews = [], []
    old = ep / "thumbnail.png"
    if old.is_file():
        shutil.copy2(old, out / "old_thumbnail.png")
        Image.open(old).convert("RGB").resize((320, 180), Image.Resampling.LANCZOS).save(out / "old_thumbnail_320.png")
        previews.append(("old", out / "old_thumbnail_320.png"))
    for layout in [x.strip() for x in args.layouts.split(",") if x.strip()]:
        img, info = th.render_thumbnail(ep, episode=episode, hook=args.headline, layout=layout,
                                        background=background)
        path = th.save_youtube_png(img, out / f"{layout}.png")
        prev = out / f"{layout}_320.png"
        img.resize((320, 180), Image.Resampling.LANCZOS).save(prev)
        previews.append((layout, prev))
        info["path"], info["preview"], info["bytes"] = str(path), str(prev), path.stat().st_size
        samples.append(info)
    sheet = Image.new("RGB", (20 + 340 * len(previews), 230), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    for i, (name, p) in enumerate(previews):
        sheet.paste(Image.open(p).convert("RGB"), (20 + i * 340, 20))
        d.text((20 + i * 340, 205), name, fill=(230, 230, 230))
    sheet.save(out / "contact_sheet.png")
    (out / "samples.json").write_text(json.dumps(samples, indent=2, default=str), encoding="utf-8")
    for s in samples:
        print(s["layout"], s["headline"], s["lines"], s["sizes"], s["bytes"], s["background"].get("query"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
