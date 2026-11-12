"""YouTube thumbnails for PROJECT GAKU (v3: headline + auto-fit + real frames).

Local only: Pillow + FFmpeg. No network, no model calls.

Headline (short thumbnail text, separate from the YouTube title), first hit wins:
  1. explicit ``hook`` argument
  2. ``episode["thumbnail_text"]`` (2-5 words; wrap the key word in *asterisks*)
  3. legacy ``episode["thumbnail_hook"]`` / ``thumb_hook``
  4. env ``GAKU_THUMBNAIL_TEXT`` / ``GAKU_THUMBNAIL_HOOK``
  5. deterministic heuristic from the topic: drop subtitle after ':' / em dash /
     '|' / '(' / sentence break, drop filler words, cut long titles at the first
     connector ("to", "for", "in", ...), cap at 5 whole words.

Text is measured with Pillow, wrapped onto at most 3 lines on word boundaries,
and shrunk until every line fits its safe box. Words are never cut or dropped by
the fitter. Layout boxes stay clear of YouTube's bottom-right duration badge.

Background: the sharpest, non-black, low-text B-roll moment from the rendered
segment clips (caption strip cropped away, code-panel clips skipped via the
pre-code baseline when present), preferring B-roll whose search query overlaps
the topic. ``episode_dir/thumbnail_bg.(png|jpg)`` overrides the frame. Falls
back to the open-title starfield.

Layouts: ``bold`` (left text over gradient, default), ``bigword`` (kicker +
giant key word), ``split`` (brand panel + image). Override: ``layout=`` or env
``GAKU_THUMBNAIL_LAYOUT``. ``episode_dir/thumbnail.png`` still overrides all
generation for uploads.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat

W, H = 1280, 720
WHITE = (255, 255, 255)
INK = (6, 8, 18)
ACCENT = (255, 206, 42)
BRAND = (124, 58, 237)
BRAND_DARK = (16, 12, 44)
MAX_PNG_BYTES = 2_000_000
MAX_HEADLINE_WORDS = 5
# YouTube draws the duration badge bottom-right; keep all text out of it.
TIMESTAMP_ZONE = (W - 260, H - 96, W, H)
LAYOUTS = ("bold", "bigword", "split")
DEFAULT_LAYOUT = "bold"

FONT_PREFERENCE = ("impact.ttf", "ariblk.ttf", "seguibl.ttf", "arialbd.ttf")
BADGE_FONT_PREFERENCE = ("seguibl.ttf", "ariblk.ttf", "arialbd.ttf", "impact.ttf")
KANJI_FONT_PREFERENCE = ("YuGothB.ttc", "msgothic.ttc")

# Legacy constant kept for callers that inspected it.
STYLE = {"plate": "best_broll_frame", "hook_words_max": MAX_HEADLINE_WORDS, "hook_words_min": 2,
         "font_preference": FONT_PREFERENCE, "layouts": LAYOUTS}

_SUBTITLE_SPLIT = re.compile(r"\s*(?::|\u2014|\u2013|\s-\s|\||\(|\[|[.?!]\s)\s*")
_FILLER = {
    "a", "an", "the", "really", "actually", "just", "very", "truly", "ultimate", "complete",
    "definitive", "technical", "beginner-friendly", "beginners", "deep", "dive", "guide",
    "explained", "introduction", "overview", "episode", "part", "everything", "need", "know",
}
_CONNECTORS = {
    "to", "for", "in", "with", "of", "from", "into", "on", "at", "by", "about", "versus",
    "vs", "and", "or", "that", "which", "your", "my", "our", "their", "this", "these",
}
_STOP = _FILLER | _CONNECTORS | {"how", "why", "what", "is", "are", "it", "its", "can", "do", "does"}


def _bare(word: str) -> str:
    return re.sub(r"[^\w'+-]", "", word).strip("'").lower()


# --------------------------------------------------------------------------- headline

def headline_from_title(title: str, max_words: int = MAX_HEADLINE_WORDS) -> str:
    """Deterministic short headline from a long episode title. Whole words only."""
    text = re.sub(r"\s+", " ", (title or "").strip())
    if not text:
        return "PROJECT GAKU"
    main = _SUBTITLE_SPLIT.split(text, maxsplit=1)[0].strip() or text
    words = main.split(" ")
    kept = [w for w in words if _bare(w) not in _FILLER] or words
    if len(kept) > max_words:
        for i, w in enumerate(kept):
            if i >= 2 and _bare(w) in _CONNECTORS:
                kept = kept[:i]
                break
    if len(kept) > max_words:
        kept = [w for w in kept if _bare(w) not in _CONNECTORS] or kept
    kept = kept[:max_words]
    while len(kept) > 1 and _bare(kept[-1]) in _CONNECTORS:
        kept.pop()
    while len(kept) > 1 and _bare(kept[0]) in {"a", "an", "the"}:
        kept.pop(0)
    return " ".join(w.strip(",;") for w in kept)


def parse_headline(text: str) -> tuple[list[str], set[int]]:
    """Uppercase words + indices of accent words (marked *like this*).

    Without markers the accent is the last content word (e.g. HOW 3D ANIMATION *WORKS*).
    """
    raw = re.sub(r"\s+", " ", (text or "").strip()).split(" ")
    words: list[str] = []
    accents: set[int] = set()
    open_mark = False
    for token in raw:
        if not token:
            continue
        starts, ends = token.startswith("*"), token.endswith("*") and len(token) > 1
        clean = token.strip("*")
        if not clean:
            continue
        if starts or open_mark:
            accents.add(len(words))
        open_mark = (open_mark or starts) and not ends
        words.append(clean.upper())
    if not words:
        words = ["PROJECT", "GAKU"]
    if not accents:
        for i in range(len(words) - 1, -1, -1):
            if _bare(words[i]) not in _STOP:
                accents.add(i)
                break
        else:
            accents.add(len(words) - 1)
    return words, accents


def resolve_hook_text(episode: dict[str, Any] | None = None, hook: str | None = None) -> str:
    """Thumbnail headline text (may contain *accent* markers)."""
    candidates = [hook]
    if episode:
        candidates += [episode.get(k) for k in ("thumbnail_text", "thumbnail_hook", "thumb_hook")]
    candidates += [os.environ.get("GAKU_THUMBNAIL_TEXT"), os.environ.get("GAKU_THUMBNAIL_HOOK")]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            text = re.sub(r"\s+", " ", c.strip())
            if len(text.split(" ")) > MAX_HEADLINE_WORDS + 1:
                text = headline_from_title(text.replace("*", ""))
            return text
    topic = (episode or {}).get("topic") or (episode or {}).get("title") or ""
    return headline_from_title(str(topic))


def _normalize_hook(text: str) -> str:  # legacy helper
    return " ".join(parse_headline(text)[0])


def _hook_from_topic(topic: str) -> str:  # legacy helper
    return headline_from_title(topic).upper()


# --------------------------------------------------------------------------- fonts

def _font_path(preference=FONT_PREFERENCE) -> Path | None:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in preference:
        p = windir / name
        if p.exists():
            return p
    return None


def load_font(size: int, preference=FONT_PREFERENCE) -> ImageFont.FreeTypeFont:
    p = _font_path(preference)
    if p is None:
        return ImageFont.load_default(size=size)
    return ImageFont.truetype(str(p), size)


def find_font(size: int) -> ImageFont.ImageFont:  # legacy name
    return load_font(size)


# --------------------------------------------------------------------------- fitting

@dataclass
class FittedText:
    lines: list[list[str]]
    size: int
    stroke: int
    font: Any
    origins: list[tuple[int, int]]           # left-baseline anchor per line
    boxes: list[tuple[int, int, int, int]]   # drawn bbox per line incl. stroke
    word_index: list[list[int]] = field(default_factory=list)

    @property
    def union(self) -> tuple[int, int, int, int]:
        xs0, ys0, xs1, ys1 = zip(*self.boxes)
        return min(xs0), min(ys0), max(xs1), max(ys1)


_MEASURE = ImageDraw.Draw(Image.new("L", (4, 4)))


def _splits(n: int, k: int):
    for cuts in combinations(range(1, n), k - 1):
        bounds = (0, *cuts, n)
        yield [(bounds[i], bounds[i + 1]) for i in range(k)]


def fit_text(words: list[str], box: tuple[int, int, int, int], *, max_lines: int = 3,
             max_size: int = 230, min_size: int = 14, stroke_ratio: float = 0.08,
             align: str = "left", valign: str = "center",
             preference=FONT_PREFERENCE) -> FittedText:
    """Largest font where all words fit `box` on <= max_lines lines (word wrap only)."""
    words = [w for w in words if w]
    if not words:
        raise ValueError("no words to fit")
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    size = max_size
    while size >= min_size:
        font = load_font(size, preference)
        stroke = max(2, round(size * stroke_ratio))
        cap_top, cap_bottom = font.getbbox("H", anchor="ls")[1], 0
        cap = cap_bottom - cap_top
        pitch = cap + 2 * stroke + max(4, round(cap * 0.16))
        best = None
        for k in range(1, min(max_lines, len(words)) + 1):
            if (k - 1) * pitch + cap + 2 * stroke > bh:
                break
            for split in _splits(len(words), k):
                texts = [" ".join(words[a:b]) for a, b in split]
                widths = [font.getlength(t) + 2 * stroke for t in texts]
                if max(widths) > bw:
                    continue
                score = (max(widths) - min(widths)) if k > 1 else 0
                if best is None or score < best[0]:
                    best = (score, split, texts)
            if best is not None:
                break
        if best is not None:
            _, split, texts = best
            origins, boxes = [], []
            for i, t in enumerate(texts):
                base = (x0 + stroke, y0 - cap_top + stroke + i * pitch)
                bb = _MEASURE.textbbox(base, t, font=font, anchor="ls", stroke_width=stroke)
                origins.append(base)
                boxes.append(bb)
            ux0, uy0, ux1, uy1 = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                                  max(b[2] for b in boxes), max(b[3] for b in boxes))
            dy = y0 - uy0 + ((bh - (uy1 - uy0)) // 2 if valign == "center" else
                             (bh - (uy1 - uy0)) if valign == "bottom" else 0)
            shifted_o, shifted_b = [], []
            for (ox, oy), bb in zip(origins, boxes):
                dx = x0 - min(b[0] for b in boxes)
                if align == "center":
                    dx = x0 + (bw - (bb[2] - bb[0])) // 2 - bb[0]
                shifted_o.append((ox + dx, oy + dy))
                shifted_b.append((bb[0] + dx, bb[1] + dy, bb[2] + dx, bb[3] + dy))
            if all(b[0] >= x0 and b[1] >= y0 and b[2] <= x1 and b[3] <= y1 for b in shifted_b):
                return FittedText([words[a:b] for a, b in split], size, stroke, font,
                                  shifted_o, shifted_b, [list(range(a, b)) for a, b in split])
        size -= max(2, size // 24)
    raise ValueError(f"cannot fit {' '.join(words)!r} into {box}")


def fit_text_safe(words, box, **kw) -> FittedText:
    try:
        return fit_text(words, box, **kw)
    except ValueError:
        kw.update(max_lines=max(kw.get("max_lines", 3), 4), min_size=8)
        return fit_text(words, box, **kw)


# --------------------------------------------------------------------------- drawing

def _draw_fitted(img: Image.Image, fitted: FittedText, accents: set[int], *,
                 fill=WHITE, accent=ACCENT, shadow=True) -> None:
    font, stroke = fitted.font, fitted.stroke
    if shadow:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        off = max(3, round(fitted.size * 0.045))
        for (ox, oy), line in zip(fitted.origins, fitted.lines):
            d.text((ox + off, oy + off), " ".join(line), font=font, anchor="ls",
                   fill=(0, 0, 0, 190), stroke_width=stroke, stroke_fill=(0, 0, 0, 190))
        layer = layer.filter(ImageFilter.GaussianBlur(max(2, fitted.size * 0.05)))
        img.alpha_composite(layer)
    draw = ImageDraw.Draw(img)
    space = font.getlength(" ")
    for (ox, oy), line, idxs in zip(fitted.origins, fitted.lines, fitted.word_index):
        x = ox
        for word, wi in zip(line, idxs):
            draw.text((x, oy), word, font=font, anchor="ls", fill=accent if wi in accents else fill,
                      stroke_width=stroke, stroke_fill=INK)
            x += font.getlength(word) + space


def _hgrad(size, x_start, x_end, a_start, a_end, color=INK) -> Image.Image:
    w, h = size
    mask = Image.new("L", (w, 1), 0)
    px = mask.load()
    for x in range(w):
        t = 0.0 if x <= x_start else 1.0 if x >= x_end else (x - x_start) / max(1, x_end - x_start)
        px[x, 0] = int(a_start + (a_end - a_start) * (t ** 1.3))
    layer = Image.new("RGBA", size, color + (0,))
    layer.putalpha(mask.resize(size))
    return layer


def _vgrad(size, y_start, y_end, a_start, a_end, color=INK) -> Image.Image:
    w, h = size
    mask = Image.new("L", (1, h), 0)
    px = mask.load()
    for y in range(h):
        t = 0.0 if y <= y_start else 1.0 if y >= y_end else (y - y_start) / max(1, y_end - y_start)
        px[0, y] = int(a_start + (a_end - a_start) * t)
    layer = Image.new("RGBA", size, color + (0,))
    layer.putalpha(mask.resize(size))
    return layer


def _draw_badge(img: Image.Image, xy=(44, 38)) -> tuple[int, int, int, int]:
    draw = ImageDraw.Draw(img)
    x, y = xy
    font = load_font(30, BADGE_FONT_PREFERENCE)
    label = "PROJECT GAKU"
    tw = font.getlength(label)
    h = 54
    kanji_path = _font_path(KANJI_FONT_PREFERENCE)
    mark = h if kanji_path else 0
    box = (x, y, int(x + mark + tw + 34), y + h)
    draw.rounded_rectangle(box, radius=12, fill=(10, 10, 24, 230))
    if kanji_path:
        draw.rounded_rectangle((x, y, x + mark, y + h), radius=12, fill=BRAND)
        kf = ImageFont.truetype(str(kanji_path), 34)
        draw.text((x + mark / 2, y + h / 2 + 1), "\u5b66", font=kf, anchor="mm", fill=WHITE)
    draw.text((x + mark + 17, y + h / 2 + 1), label, font=font, anchor="lm", fill=WHITE)
    return box


# --------------------------------------------------------------------------- backgrounds

def _fit_cover(img: Image.Image, tw: int, th: int) -> Image.Image:
    iw, ih = img.size
    scale = max(tw / iw, th / ih)
    nw, nh = max(tw, round(iw * scale)), max(th, round(ih * scale))
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _ffmpeg_frame(src: Path, ss: float, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{ss:.3f}", "-i", str(src),
           "-frames:v", "1", "-q:v", "2", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and dest.exists()


def find_starfield_plate(episode_dir: Path) -> Path:
    episode_dir = Path(episode_dir)
    cache = episode_dir / "thumbnails_audit" / "_frames" / "plate_open_0.0.png"
    if cache.exists():
        return cache
    for src in sorted((episode_dir / "visuals").glob("*open_title*.mp4")) + [episode_dir / "podcast.mp4"]:
        if src.exists() and _ffmpeg_frame(src, 0.0, cache):
            return cache
    raise FileNotFoundError(f"no starfield plate under {episode_dir}")


def score_frame(img: Image.Image) -> dict[str, float]:
    """Higher is better: sharp, colourful, mid-bright, little white text."""
    small = img.convert("RGB").resize((320, 180))
    gray = small.convert("L")
    edges = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0]
    bright = ImageStat.Stat(gray).mean[0]
    sat = ImageStat.Stat(small.convert("HSV").split()[1]).mean[0]
    hist = gray.histogram()
    white = sum(hist[236:]) / (320 * 180)
    dark = sum(hist[:18]) / (320 * 180)
    score = edges * 1.6 + sat * 0.35 - max(0, 45 - bright) * 2.5 - max(0, bright - 190) * 1.5
    score -= white * 900 + max(0, dark - 0.35) * 200
    return {"score": round(score, 2), "edges": round(edges, 2), "bright": round(bright, 1),
            "sat": round(sat, 1), "white_frac": round(white, 4), "dark_frac": round(dark, 3)}


_SYNONYMS = {
    "coding": "code", "programming": "code", "programmers": "code", "programmer": "code",
    "developers": "code", "developer": "code", "software": "code", "scripting": "code",
    "script": "code", "python": "code", "javascript": "code", "js": "code",
    "animators": "animation", "animator": "animation", "animated": "animation",
    "animating": "animation", "render": "3d", "rendering": "3d", "modeling": "3d",
    "blender": "3d", "cgi": "3d", "strategies": "strategy", "organizations": "organization",
    "teams": "team", "workers": "work", "employees": "work", "office": "work",
}


def _tokens(text: str) -> set[str]:
    out = set()
    for t in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if t in _STOP or len(t) < 2:
            continue
        out.add(_SYNONYMS.get(t, t.rstrip("s") if len(t) > 4 else t))
    return out


def _clip_for(episode_dir: Path, index: int, visual: dict[str, Any]) -> Path | None:
    vid = visual.get("visual_id") or ""
    names = [f"visual_{index:03d}_{vid}.mp4", f"{vid}.mp4"]
    for name in names:  # pre-code-panel baseline avoids code-heavy frames
        cand = episode_dir / "code_examples" / "baseline" / name
        if cand.exists():
            return cand
    if visual.get("path") and Path(visual["path"]).exists():
        return Path(visual["path"])
    for name in names:
        cand = episode_dir / "visuals" / name
        if cand.exists():
            return cand
    return None


def broll_moments(episode_dir: Path, topic: str = "", limit: int = 14) -> list[dict[str, Any]]:
    """B-roll windows with no card/code/diagram overlay, most topic-relevant first."""
    episode_dir = Path(episode_dir)
    mpath = episode_dir / "visual_manifest.json"
    if not mpath.exists():
        return []
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    topic_tokens = _tokens(topic)
    moments = []
    for i, v in enumerate(manifest.get("visuals") or [], 1):
        if v.get("template") != "SegmentCard":
            continue
        clip = _clip_for(episode_dir, i, v)
        beats = (v.get("data") or {}).get("beats") or []
        busy = [(b.get("show_ms", 0) - 800, b.get("hide_ms", 0) + 800) for b in beats
                if b.get("type") not in ("broll",)]
        dur = int(v.get("end_ms", 0)) - int(v.get("start_ms", 0))
        for b in beats:
            if b.get("type") != "broll" or clip is None:
                continue
            s, e = int(b.get("show_ms", 0)), int(b.get("hide_ms", 0))
            for t in (s + (e - s) * 0.5, s + (e - s) * 0.3):
                if 1500 < t < dur - 1500 and not any(a <= t <= z for a, z in busy):
                    rel = len(topic_tokens & _tokens(b.get("query", "")))
                    moments.append({"clip": str(clip), "t": t / 1000, "query": b.get("query", ""),
                                    "photo_id": b.get("photo_id"), "relevance": rel})
                    break
    seen, unique = set(), []
    for m in sorted(moments, key=lambda m: (-m["relevance"], m["t"])):
        key = m.get("photo_id") or (m["clip"], round(m["t"]))
        if key not in seen:
            seen.add(key)
            unique.append(m)
    # Keep relevance order but spread across clips.
    picked, per_clip = [], {}
    for m in unique:
        if per_clip.get(m["clip"], 0) < 2:
            picked.append(m)
            per_clip[m["clip"]] = per_clip.get(m["clip"], 0) + 1
        if len(picked) >= limit:
            break
    return picked


def _crop_above_captions(frame: Image.Image) -> Image.Image:
    """Drop chapter label (top) and caption/speaker strip (bottom ~36%)."""
    fw, fh = frame.size
    top, bottom = int(fh * 0.075), int(fh * 0.635)
    band = frame.crop((0, top, fw, bottom))
    bh = band.size[1]
    cw = min(fw, int(bh * 16 / 9))
    best = None
    for left in (0, (fw - cw) // 2, fw - cw):
        crop = band.crop((left, 0, left + cw, bh))
        s = score_frame(crop)["edges"]
        if best is None or s > best[0]:
            best = (s, crop)
    return best[1]


def select_background(episode_dir: Path | str, episode: dict[str, Any] | None = None,
                      *, limit: int = 14) -> tuple[Image.Image, dict[str, Any]]:
    episode_dir = Path(episode_dir)
    for name in ("thumbnail_bg.png", "thumbnail_bg.jpg"):
        p = episode_dir / name
        if p.is_file():
            return _fit_cover(Image.open(p).convert("RGB"), W, H), {"source": str(p), "kind": "override"}
    topic = " ".join([str((episode or {}).get("topic", "")), str((episode or {}).get("thumbnail_text") or "")]
                     + [str(sg.get("title", "")) for sg in (episode or {}).get("segments", [])])
    cache = episode_dir / "thumbnails_audit" / "_frames"
    best = None
    for k, m in enumerate(broll_moments(episode_dir, topic, limit)):
        dest = cache / f"v3_{Path(m['clip']).stem}_{m['t']:.2f}.jpg"
        if not dest.exists() and not _ffmpeg_frame(Path(m["clip"]), m["t"], dest):
            continue
        crop = _crop_above_captions(Image.open(dest).convert("RGB"))
        s = score_frame(crop)
        total = s["score"] + m["relevance"] * 12
        if best is None or total > best[0]:
            best = (total, crop, {**m, **s, "frame": str(dest), "kind": "broll", "total": round(total, 2)})
    if best is not None:
        img = _fit_cover(best[1], W, H)
        img = ImageEnhance.Brightness(img).enhance(1.12)
        img = ImageEnhance.Contrast(img).enhance(1.12)
        img = ImageEnhance.Color(img).enhance(1.2)
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=3))
        return img, best[2]
    plate = find_starfield_plate(episode_dir)
    return _fit_cover(Image.open(plate).convert("RGB"), W, H), {"source": str(plate), "kind": "starfield"}


def load_dark_plate(episode_dir: Path) -> Image.Image:  # legacy helper
    return select_background(episode_dir)[0]


# --------------------------------------------------------------------------- motif

_MOTIF_WORDS = {"3d", "animation", "rig", "rigging", "skeleton", "skeletal", "keyframe", "keyframes", "mocap"}


def wants_rig_motif(episode: dict[str, Any] | None, headline: str = "") -> bool:
    explicit = (episode or {}).get("thumbnail_motif")
    if isinstance(explicit, str):
        return explicit.strip().lower() == "rig"
    words = set(re.findall(r"[a-z0-9]+", ((episode or {}).get("topic", "") + " " + headline).lower()))
    return bool(words & _MOTIF_WORDS)


def draw_rig_motif(img: Image.Image, region: tuple[int, int, int, int]) -> None:
    """Emoji-free accent: glowing wireframe armature + keyframe timeline."""
    x0, y0, x1, y1 = region
    w, h = x1 - x0, y1 - y0
    cx = x0 + w * 0.5
    def P(px, py):
        return (x0 + px * w, y0 + py * h)
    joints = {
        "head": P(.50, .07), "neck": P(.50, .18), "chest": P(.50, .30), "pelvis": P(.50, .52),
        "l_sh": P(.34, .22), "l_el": P(.20, .36), "l_wr": P(.12, .22),
        "r_sh": P(.66, .22), "r_el": P(.80, .38), "r_wr": P(.90, .50),
        "l_hip": P(.42, .55), "l_kn": P(.36, .72), "l_an": P(.30, .88),
        "r_hip": P(.58, .55), "r_kn": P(.68, .70), "r_an": P(.74, .86),
    }
    bones = [("head", "neck"), ("neck", "chest"), ("chest", "pelvis"), ("neck", "l_sh"), ("l_sh", "l_el"),
             ("l_el", "l_wr"), ("neck", "r_sh"), ("r_sh", "r_el"), ("r_el", "r_wr"), ("pelvis", "l_hip"),
             ("l_hip", "l_kn"), ("l_kn", "l_an"), ("pelvis", "r_hip"), ("r_hip", "r_kn"), ("r_kn", "r_an")]
    cyan = (64, 224, 255)
    shade = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shade).ellipse([x0 - w * 0.12, y0 - h * 0.05, x1 + w * 0.12, y1 + h * 0.08],
                                  fill=(4, 8, 22, 175))
    img.alpha_composite(shade.filter(ImageFilter.GaussianBlur(60)))
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    lines = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(lines)
    # perspective floor grid
    floor_y = y0 + h * 0.90
    for i in range(-5, 6):
        ld.line([(cx + i * w * 0.07, floor_y), (cx + i * w * 0.20, y1 + h * 0.08)], fill=cyan + (70,), width=2)
    for j, f in enumerate((0.0, 0.04, 0.09, 0.15)):
        yy = floor_y + h * f
        span = w * (0.36 + f * 2.2)
        ld.line([(cx - span, yy), (cx + span, yy)], fill=cyan + (70 - j * 8,), width=2)
    for a, b in bones:
        (ax, ay), (bx, by) = joints[a], joints[b]
        gd.line([(ax, ay), (bx, by)], fill=cyan + (200,), width=20)
        # octahedral bone: thick near parent, thin at child
        dx, dy = bx - ax, by - ay
        ln = max(1.0, (dx * dx + dy * dy) ** 0.5)
        nx, ny = -dy / ln, dx / ln
        mx, my = ax + dx * 0.22, ay + dy * 0.22
        k = min(16, ln * 0.14)
        ld.polygon([(ax, ay), (mx + nx * k, my + ny * k), (bx, by), (mx - nx * k, my - ny * k)],
                   fill=(20, 40, 70, 200), outline=cyan + (255,), width=3)
    for name, (jx, jy) in joints.items():
        r = 15 if name == "head" else 9
        gd.ellipse([jx - r - 8, jy - r - 8, jx + r + 8, jy + r + 8], fill=ACCENT + (170,))
        ld.ellipse([jx - r, jy - r, jx + r, jy + r], fill=(255, 245, 210, 255), outline=ACCENT + (255,), width=3)
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10)))
    img.alpha_composite(lines)


# --------------------------------------------------------------------------- layouts

LAYOUT_BOXES = {
    "bold": {"text": (56, 118, 770, 612)},
    "bigword": {"kicker": (56, 120, 1060, 230), "big": (50, 236, 1150, 520), "tail": (56, 528, 980, 612)},
    "split": {"text": (52, 120, 610, 612)},
}


def compose(background: Image.Image, headline: str, layout: str = DEFAULT_LAYOUT, *,
            motif: bool = False) -> tuple[Image.Image, dict[str, Any]]:
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}; choose from {LAYOUTS}")
    words, accents = parse_headline(headline)
    bg = _fit_cover(background.convert("RGB"), W, H).convert("RGBA")
    info: dict[str, Any] = {"layout": layout, "words": words, "accents": sorted(accents), "text_boxes": []}

    if layout == "split":
        img = Image.new("RGBA", (W, H), BRAND_DARK + (255,))
        right = _fit_cover(background.convert("RGB"), 760, H).convert("RGBA")
        img.alpha_composite(right, (W - 760, 0))
        poly = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pd = ImageDraw.Draw(poly)
        pd.polygon([(0, 0), (700, 0), (600, H), (0, H)], fill=BRAND_DARK + (255,))
        pd.polygon([(700, 0), (722, 0), (622, H), (600, H)], fill=ACCENT + (255,))
        img.alpha_composite(poly)
        img.alpha_composite(_vgrad((W, H), int(H * 0.72), H, 0, 120))
    else:
        img = bg
        if layout == "bold":
            img.alpha_composite(_hgrad((W, H), int(W * 0.34), int(W * 0.86), 238, 0))
            img.alpha_composite(_vgrad((W, H), int(H * 0.6), H, 0, 150))
        else:
            img.alpha_composite(_vgrad((W, H), 0, H, 70, 225))
            img.alpha_composite(_hgrad((W, H), 0, W, 120, 20))

    if motif and layout in ("bold", "split"):
        region = (800, 70, 1230, 610) if layout == "bold" else (760, 70, 1210, 610)
        if layout == "bold":
            img.alpha_composite(_hgrad((W, H), int(W * 0.55), W, 0, 120))
        draw_rig_motif(img, region)
        info["motif"] = {"kind": "rig", "region": region}

    if layout == "bigword":
        a = min(accents)
        groups = {"kicker": words[:a], "big": [words[a]], "tail": words[a + 1:]}
        offset = {"kicker": 0, "big": a, "tail": a + 1}
        if not groups["kicker"] and groups["tail"]:
            groups["kicker"], groups["tail"], offset["kicker"] = groups["tail"], [], a + 1
        for key in ("kicker", "big", "tail"):
            if not groups[key]:
                continue
            fitted = fit_text_safe(groups[key], LAYOUT_BOXES["bigword"][key], max_lines=1 if key != "big" else 2,
                                   max_size=300 if key == "big" else 96)
            fitted.word_index = [[i + offset[key] for i in idx] for idx in fitted.word_index]
            _draw_fitted(img, fitted, accents)
            info["text_boxes"] += fitted.boxes
            info.setdefault("sizes", {})[key] = fitted.size
            info.setdefault("lines", []).extend(fitted.lines)
    else:
        fitted = fit_text_safe(words, LAYOUT_BOXES[layout]["text"], max_lines=3, max_size=210)
        _draw_fitted(img, fitted, accents)
        info["text_boxes"] += fitted.boxes
        info["sizes"] = {"text": fitted.size}
        info["lines"] = fitted.lines
        ux0, _, ux1, uy1 = fitted.union
        bar = (ux0, min(uy1 + 18, H - 110), ux0 + 150, min(uy1 + 32, H - 96))
        ImageDraw.Draw(img).rounded_rectangle(bar, radius=6, fill=ACCENT)
        info["accent_bar"] = bar
    info["badge"] = _draw_badge(img)
    return img.convert("RGB"), info


def compose_one_hook(plate: Image.Image, hook: str) -> Image.Image:  # legacy entry point
    return compose(plate, hook, DEFAULT_LAYOUT)[0]


# --------------------------------------------------------------------------- output

def episode_override_path(episode_dir: Path | str) -> Path:
    return Path(episode_dir) / "thumbnail.png"


def save_youtube_png(img: Image.Image, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rgb = img.convert("RGB")
    if path.suffix.lower() in (".jpg", ".jpeg"):
        for q in (92, 88, 82, 75):
            rgb.save(str(path), "JPEG", quality=q, optimize=True, progressive=True)
            if path.stat().st_size <= MAX_PNG_BYTES:
                break
    else:
        rgb.save(str(path), "PNG", optimize=True)
        if path.stat().st_size > MAX_PNG_BYTES:
            for colors in (256, 192, 128):
                rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT,
                             dither=Image.Dither.FLOYDSTEINBERG).save(str(path), "PNG", optimize=True)
                if path.stat().st_size <= MAX_PNG_BYTES:
                    break
    print(f"[thumb] saved {path} ({path.stat().st_size} bytes)")
    return path


def render_thumbnail(episode_dir: Path | str, *, episode: dict[str, Any] | None = None,
                     hook: str | None = None, layout: str | None = None,
                     background: tuple[Image.Image, dict[str, Any]] | None = None
                     ) -> tuple[Image.Image, dict[str, Any]]:
    ep = Path(episode_dir)
    if episode is None and (ep / "episode.json").exists():
        episode = json.loads((ep / "episode.json").read_text(encoding="utf-8"))
    headline = resolve_hook_text(episode, hook)
    layout = layout or os.environ.get("GAKU_THUMBNAIL_LAYOUT") or DEFAULT_LAYOUT
    bg, bg_info = background or select_background(ep, episode)
    img, info = compose(bg, headline, layout, motif=wants_rig_motif(episode, headline))
    info.update(headline=headline, background=bg_info)
    return img, info


def generate_hybrid_thumbnail(episode_dir: Path | str, *, episode: dict[str, Any] | None = None,
                              hook: str | None = None, out_path: Path | str | None = None,
                              layout: str | None = None) -> Path:
    img, info = render_thumbnail(episode_dir, episode=episode, hook=hook, layout=layout)
    print(f"[thumb] layout={info['layout']} headline={info['headline']!r} bg={info['background'].get('kind')}")
    dest = Path(out_path) if out_path else episode_override_path(episode_dir)
    return save_youtube_png(img, dest)


def resolve_thumbnail_path(episode_dir: Path | str | None, *, generate: bool = True,
                           episode: dict[str, Any] | None = None, hook: str | None = None,
                           out_path: Path | str | None = None) -> Path | None:
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
    try:
        return generate_hybrid_thumbnail(ep, episode=episode, hook=hook,
                                         out_path=Path(out_path) if out_path else override)
    except Exception as exc:
        print(f"[thumb] generate failed: {exc}")
        return None


def set_thumbnail_on_youtube(youtube, video_id: str, png_path: Path | str) -> dict[str, Any]:
    """Call thumbnails().set. Returns API response (+ default maxres URL)."""
    from googleapiclient.http import MediaFileUpload

    path = Path(png_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    media = MediaFileUpload(str(path), mimetype=mime, resumable=True)
    resp = youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
    items = (resp or {}).get("items") or []
    urls = items[0] if items else {}
    maxres = (urls.get("maxres") or {}).get("url") or f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
    print(f"[thumb] thumbnails.set ok -> {maxres}")
    return {"thumbnail_set": True, "thumbnail_url": maxres, "thumbnail_response": resp}
