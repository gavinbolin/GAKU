"""Hybrid YouTube thumbnails for PROJECT GAKU.

Default: cinematic dark plate from open-title starfield (or visual_001), plus
ONE big Impact-style hook line (about 2-4 words), high contrast, no badges.

Override: if episode_dir/thumbnail.png exists, use it as-is (canonical path for
hand-picked thumbs such as v2-A "MOST FAIL").

Hook text resolution (first hit wins):
  1. episode["thumbnail_hook"]
  2. env GAKU_THUMBNAIL_HOOK
  3. crude fallback from topic (uppercase first 2-4 content words) — weak;
     prefer an explicit hook. Full auto punchy-hook generation is a gap.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

W, H = 1280, 720
WHITE = (255, 255, 255)
MAX_PNG_BYTES = 2_000_000

# Style defaults matching audit v2-A
STYLE = {
    "plate": "cinematic_dark_starfield",
    "brightness": 0.62,
    "contrast": 1.35,
    "color": 1.22,
    "vignette_strength": 0.88,
    "hook_words_max": 4,
    "hook_words_min": 2,
    "font_preference": ("impact.ttf", "arialbd.ttf", "segoeuib.ttf", "calibrib.ttf"),
    "no_badges": True,
    "no_small_labels": True,
}


def episode_override_path(episode_dir: Path | str) -> Path:
    return Path(episode_dir) / "thumbnail.png"


def resolve_thumbnail_path(
    episode_dir: Path | str | None,
    *,
    generate: bool = True,
    episode: dict[str, Any] | None = None,
    hook: str | None = None,
    out_path: Path | str | None = None,
) -> Path | None:
    """Return PNG to upload: override if present, else optionally generate."""
    if episode_dir is None:
        return None
    ep = Path(episode_dir)
    override = episode_override_path(ep)
    if override.is_file() and override.stat().st_size > 0:
        print(f"[thumb] using episode override {override}")
        return override
    if not generate:
        return None
    dest = Path(out_path) if out_path else override
    try:
        return generate_hybrid_thumbnail(ep, episode=episode, hook=hook, out_path=dest)
    except Exception as exc:
        print(f"[thumb] generate failed: {exc}")
        return None


def resolve_hook_text(
    episode: dict[str, Any] | None = None,
    hook: str | None = None,
) -> str:
    if hook and str(hook).strip():
        return _normalize_hook(str(hook))
    if episode:
        for key in ("thumbnail_hook", "thumb_hook", "hook"):
            val = episode.get(key)
            if isinstance(val, str) and val.strip():
                return _normalize_hook(val)
    env = (os.environ.get("GAKU_THUMBNAIL_HOOK") or "").strip()
    if env:
        return _normalize_hook(env)
    topic = (episode or {}).get("topic") or (episode or {}).get("title") or ""
    return _hook_from_topic(str(topic))


def _normalize_hook(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    words = text.split()
    if len(words) > STYLE["hook_words_max"]:
        words = words[: STYLE["hook_words_max"]]
    return " ".join(words).upper()


def _hook_from_topic(topic: str) -> str:
    """Weak fallback — not punchy LLM hooks. Prefer episode.thumbnail_hook."""
    stop = {
        "A", "AN", "THE", "AND", "OR", "OF", "FOR", "TO", "IN", "ON", "WITH",
        "THIS", "THAT", "CAN", "HELP", "HOW", "WHY", "WHAT", "FROM", "INTO",
        "|", "PROJECT", "GAKU", "HBR",
    }
    cleaned = re.sub(r"[^\w\s'-]", " ", topic.upper())
    words = [w for w in cleaned.split() if w and w not in stop and len(w) > 1]
    if not words:
        words = [w for w in cleaned.split() if w]
    take = words[: STYLE["hook_words_max"]] or ["GAKU"]
    if len(take) < STYLE["hook_words_min"] and len(words) >= STYLE["hook_words_min"]:
        take = words[: STYLE["hook_words_min"]]
    return " ".join(take)


def find_font(size: int) -> ImageFont.ImageFont:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in STYLE["font_preference"]:
        p = windir / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def _ffmpeg_frame(src: Path, ss: float, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-ss", str(ss), "-i", str(src),
        "-frames:v", "1", "-q:v", "2", str(dest),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and dest.exists()


def _fit_cover(img: Image.Image, tw: int, th: int) -> Image.Image:
    iw, ih = img.size
    scale = max(tw / iw, th / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def find_starfield_plate(episode_dir: Path) -> Path:
    """Prefer t=0 open-title (clean starfield before title bake-in)."""
    cache = episode_dir / "thumbnails_audit" / "_frames" / "plate_open_0.0.png"
    open_mp4 = episode_dir / "visuals" / "visual_001_open_title.mp4"
    podcast_mp4 = episode_dir / "podcast.mp4"
    if cache.exists():
        return cache
    if open_mp4.exists():
        if _ffmpeg_frame(open_mp4, 0.0, cache):
            return cache
    if podcast_mp4.exists():
        if _ffmpeg_frame(podcast_mp4, 0.0, cache):
            return cache
    # Last-resort: any prior audit plate
    for cand in (
        episode_dir / "thumbnails_audit" / "_frames" / "cand_visual_001_open_title_1.8.png",
        episode_dir / "thumbnails_audit" / "thumbnail_A_frame.png",
    ):
        if cand.exists():
            return cand
    raise FileNotFoundError(f"no starfield plate under {episode_dir}")


def load_dark_plate(episode_dir: Path) -> Image.Image:
    plate_path = find_starfield_plate(episode_dir)
    print(f"[thumb] plate {plate_path}")
    img = Image.open(plate_path).convert("RGB")
    img = _fit_cover(img, W, H)
    img = ImageEnhance.Brightness(img).enhance(STYLE["brightness"])
    img = ImageEnhance.Contrast(img).enhance(STYLE["contrast"])
    img = ImageEnhance.Color(img).enhance(STYLE["color"])
    return img


def _vignette(base: Image.Image, strength: float | None = None) -> Image.Image:
    strength = STYLE["vignette_strength"] if strength is None else strength
    mask = Image.new("L", (W, H), 0)
    for i in range(16):
        pad_x = int(80 + i * 22)
        pad_y = int(50 + i * 14)
        if W - pad_x <= pad_x or H - pad_y <= pad_y:
            break
        alpha = int(strength * 255 * ((1 - i / 16) ** 1.35) * 0.14)
        if alpha <= 0:
            continue
        layer = Image.new("L", (W, H), 0)
        ld = ImageDraw.Draw(layer)
        ld.ellipse([pad_x, pad_y, W - pad_x, H - pad_y], fill=alpha)
        mask = Image.composite(layer, mask, layer)
    dark = Image.new("RGBA", (W, H), (2, 4, 12, 0))
    dark.putalpha(mask)
    return Image.alpha_composite(base.convert("RGBA"), dark).convert("RGB")


def _fit_hook_font(text: str, max_w: int, max_h: int, start: int = 280):
    size = start
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    while size >= 40:
        font = find_font(size)
        bb = tmp.textbbox((0, 0), text, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        if tw <= max_w and th <= max_h:
            return font
        size -= 4
    return find_font(40)


def _draw_shadowed(draw: ImageDraw.ImageDraw, xy, text, font, fill):
    x, y = xy
    for dx, dy in ((7, 7), (5, 5), (3, 3), (-2, 3), (3, -2)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
    for dx, dy in ((-4, 0), (4, 0), (0, -4), (0, 4), (-3, -3), (3, 3), (-3, 3), (3, -3)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=fill)


def compose_one_hook(plate: Image.Image, hook: str) -> Image.Image:
    """v2-A style: single giant white hook, no badges/labels."""
    img = _vignette(plate.copy())
    draw = ImageDraw.Draw(img)
    line = _normalize_hook(hook)
    font = _fit_hook_font(line, max_w=W - 50, max_h=int(H * 0.48), start=280)
    bb = draw.textbbox((0, 0), line, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    x = (W - tw) // 2
    y = (H - th) // 2 - 16
    _draw_shadowed(draw, (x, y), line, font, WHITE)
    return img


def save_youtube_png(img: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rgb = img.convert("RGB")
    rgb.save(str(path), "PNG", optimize=True, compress_level=9)
    size = path.stat().st_size
    if size > MAX_PNG_BYTES:
        for colors in (192, 128, 96):
            pal = rgb.convert("P", palette=Image.ADAPTIVE, colors=colors)
            pal.save(str(path), "PNG", optimize=True)
            size = path.stat().st_size
            if size <= MAX_PNG_BYTES:
                break
    print(f"[thumb] saved {path} ({size} bytes)")
    return path


def generate_hybrid_thumbnail(
    episode_dir: Path | str,
    *,
    episode: dict[str, Any] | None = None,
    hook: str | None = None,
    out_path: Path | str | None = None,
) -> Path:
    ep = Path(episode_dir)
    hook_text = resolve_hook_text(episode, hook)
    plate = load_dark_plate(ep)
    composed = compose_one_hook(plate, hook_text)
    dest = Path(out_path) if out_path else episode_override_path(ep)
    return save_youtube_png(composed, dest)


def set_thumbnail_on_youtube(youtube, video_id: str, png_path: Path | str) -> dict[str, Any]:
    """Call thumbnails().set. Returns API response (+ default maxres URL)."""
    from googleapiclient.http import MediaFileUpload

    path = Path(png_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    media = MediaFileUpload(str(path), mimetype="image/png", resumable=True)
    resp = youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
    items = (resp or {}).get("items") or []
    urls = items[0] if items else {}
    maxres = (urls.get("maxres") or {}).get("url") or f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
    print(f"[thumb] thumbnails.set ok -> {maxres}")
    return {"thumbnail_set": True, "thumbnail_url": maxres, "thumbnail_response": resp}
