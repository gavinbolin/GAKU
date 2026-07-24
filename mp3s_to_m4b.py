"""Compile a folder of chapter MP3s into a single .m4b audiobook.

Unlike audiobook.py (which SYNTHESIZES narration from a PDF via TTS), this
takes audio you already have — e.g. a downloaded audiobook split into one MP3
per chapter — and packages it into an audiobook-format .m4b with:
  - one chapter marker per source file (title from the filename)
  - an embedded cover image
  - title / author metadata

Reuses m4b_export.build_audiobook_m4b for the actual mux.

Usage:
  python mp3s_to_m4b.py --dir "C:/path/to/mp3s" --title "The Hobbit" --author "J.R.R. Tolkien"
  # cover auto-detected as cover.jpg/png/jpeg in --dir unless --cover given
  # output defaults to <dir>/<title>.m4b unless --out given
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from m4b_export import Chapter, build_audiobook_m4b  # noqa: E402


def natural_key(p: Path) -> list:
    """Sort key that orders 'Chapter 2' before 'Chapter 10' (numeric-aware)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def probe_duration_ms(path: Path) -> int:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path.name}: {proc.stderr.strip()[-400:]}")
    return int(float(proc.stdout.strip()) * 1000)


_YEAR_TAG_RE = re.compile(r"\s*\((?:19|20)\d{2}\)\s*$")
_TRAIL_NUM_RE = re.compile(r"\s+\d{3,4}\s*$")  # stray bitrate tags like " 256"


def chapter_title(p: Path, strip_tags: bool = False) -> str:
    """Filename stem as the chapter title.

    With strip_tags, remove trailing version cruft — '(2014)' year tags and
    stray 3-4 digit bitrate suffixes — so 'The Council of Elrond 256' becomes
    'The Council of Elrond' and 'A Long-Expected Party (2014)' loses its tag."""
    title = p.stem.strip()
    if strip_tags:
        title = _YEAR_TAG_RE.sub("", title)
        title = _TRAIL_NUM_RE.sub("", title)
        title = title.strip()
    return title


def _probe_rate_channels(path: Path) -> tuple[str, str]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    parts = r.stdout.split()
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "")


def concat_mp3s(mp3s: list[Path], work_dir: Path) -> Path:
    """Concatenate chapter MP3s into one file, returning the created path.

    Fast path: if every input shares sample-rate + channels, stream-copy them
    with the concat demuxer into a combined .mp3 (lossless, instant).

    Robust path: if sample rates differ (common in ripped audiobooks — one
    chapter at 48kHz among 44.1kHz, etc.), resample everything to the majority
    rate and concat via the concat filter into a lossless .flac intermediate.
    That keeps the only lossy step the final AAC encode, and avoids the pitch/
    glitch artifacts a stream-copy would produce across a rate boundary."""
    from collections import Counter

    params = [_probe_rate_channels(m) for m in mp3s]
    uniform = len(set(params)) == 1

    if uniform:
        out_path = work_dir / "combined.mp3"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as lf:
            list_path = Path(lf.name)
            for m in mp3s:
                safe = str(m.resolve()).replace("\\", "/").replace("'", "'\\''")
                lf.write(f"file '{safe}'\n")
        try:
            proc = subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "concat", "-safe", "0", "-i", str(list_path),
                 "-c", "copy", str(out_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg concat (copy) failed: {proc.stderr.strip()[-800:]}")
        finally:
            list_path.unlink(missing_ok=True)
        return out_path

    # Mixed sample rates → resample-concat to a lossless FLAC intermediate.
    target_rate = Counter(r for r, _ in params).most_common(1)[0][0] or "44100"
    print(f"[m4b] mixed sample rates detected → resampling all to {target_rate} Hz (lossless concat)",
          file=sys.stderr)
    out_path = work_dir / "combined.flac"
    inputs: list[str] = []
    for m in mp3s:
        inputs += ["-i", str(m)]
    n = len(mp3s)
    # Force uniform rate/format/layout per input, then concat.
    pre = "".join(
        f"[{i}:a]aresample={target_rate},aformat=sample_fmts=s16:channel_layouts=stereo[a{i}];"
        for i in range(n)
    )
    chain = "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
         *inputs, "-filter_complex", pre + chain, "-map", "[out]",
         "-sample_fmt", "s16", str(out_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat (resample) failed: {proc.stderr.strip()[-1200:]}")
    return out_path


def find_cover(directory: Path) -> Path | None:
    for name in ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png"):
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile chapter MP3s into a single .m4b audiobook.")
    parser.add_argument("--dir", required=True, help="Folder containing the chapter MP3s")
    parser.add_argument("--title", default="", help="Audiobook title metadata")
    parser.add_argument("--author", default="", help="Author / narrator metadata (artist tag)")
    parser.add_argument("--cover", default=None, help="Cover image path (auto-detected in --dir if omitted)")
    parser.add_argument("--out", default=None, help="Output .m4b path (defaults to <dir>/<title>.m4b)")
    parser.add_argument("--bitrate", default="64k", help="AAC bitrate (default 64k; try 96k to match a 96k MP3 source)")
    parser.add_argument("--pattern", default="*.mp3", help="Glob for chapter files (default: *.mp3)")
    parser.add_argument("--strip-tags", action="store_true", help="Strip trailing '(2014)' year tags and stray bitrate numbers from chapter titles")
    args = parser.parse_args()

    src_dir = Path(args.dir).resolve()
    if not src_dir.is_dir():
        print(f"Not a directory: {src_dir}", file=sys.stderr)
        return 2

    mp3s = sorted(src_dir.glob(args.pattern), key=natural_key)
    if not mp3s:
        print(f"No files matching {args.pattern!r} in {src_dir}", file=sys.stderr)
        return 2

    title = args.title or src_dir.name
    out_path = Path(args.out).resolve() if args.out else src_dir / f"{title}.m4b"

    cover = Path(args.cover).resolve() if args.cover else find_cover(src_dir)
    if cover and not cover.exists():
        print(f"[m4b] WARNING: cover not found at {cover}; continuing without it", file=sys.stderr)
        cover = None

    print(f"[m4b] {len(mp3s)} chapters → {out_path}", file=sys.stderr)

    # 1. Probe each chapter's duration and build the chapter marker list.
    chapters: list[Chapter] = []
    total_ms = 0
    for m in mp3s:
        dur = probe_duration_ms(m)
        total_ms += dur
        title_str = chapter_title(m, strip_tags=args.strip_tags)
        chapters.append(Chapter(title=title_str, duration_ms=dur))
        print(f"[m4b]   {title_str}  ({dur / 1000 / 60:.1f} min)", file=sys.stderr)
    print(f"[m4b] total runtime: {total_ms / 1000 / 3600:.2f} h", file=sys.stderr)

    # 2. Concatenate the MP3s into one intermediate file (stream-copy if
    #    uniform, resample-concat if sample rates differ).
    with tempfile.TemporaryDirectory(prefix="m4b_concat_") as td:
        print("[m4b] concatenating chapters…", file=sys.stderr)
        combined = concat_mp3s(mp3s, Path(td))

        # 3. Mux to .m4b (AAC) with chapter markers + cover via GAKU's helper.
        print(f"[m4b] encoding to m4b (aac {args.bitrate}) + chapter markers…", file=sys.stderr)
        build_audiobook_m4b(
            audio_path=combined,
            chapters=chapters,
            out_path=out_path,
            title=title,
            artist=args.author,
            cover_image=cover,
            bitrate=args.bitrate,
        )

    size_mb = out_path.stat().st_size / (1024 * 1024) if out_path.exists() else 0
    print(f"\n[m4b] DONE: {out_path}  ({size_mb:.1f} MB, {len(chapters)} chapters)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
