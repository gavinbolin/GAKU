"""M4B export helpers for PROJECT GAKU.

Builds an audiobook-format `.m4b` (AAC-in-MP4) with:
  - optional chapter markers (start/end ms + title)
  - optional embedded cover image
  - ID3-style title/author metadata

All work is done by ffmpeg/ffprobe via subprocess — no extra Python deps.

Two entry points:
  - build_audiobook_m4b(...)   chapters + cover, called from audiobook.py
  - convert_to_m4b(...)        single audio file -> .m4b, called from app.py
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Chapter:
    """One chapter entry for the m4b metadata file."""
    title: str
    duration_ms: int  # length of THIS chapter (we accumulate into start/end)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run an ffmpeg/ffprobe command, capturing both streams. Raises on non-zero exit."""
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(
            f"{cmd[0]} exited {proc.returncode}\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stderr: {proc.stderr.strip()[-2000:]}"
        )
    return proc


def probe_duration_ms(audio_path: Path) -> int:
    """Return the duration of an audio file in milliseconds via ffprobe."""
    proc = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ])
    return int(float(proc.stdout.strip()) * 1000)


def extract_cover_from_video(video_path: Path, out_jpg: Path) -> Path:
    """Pull the first frame of an MP4 (e.g. book_cover.mp4) as a JPEG."""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(out_jpg),
    ])
    return out_jpg


def write_ffmetadata(
    out_path: Path,
    title: str = "",
    artist: str = "",
    album: str = "",
    chapters: list[Chapter] | None = None,
) -> Path:
    """Write an ffmpeg `;FFMETADATA1` file with title/artist + chapter markers.

    Chapters are pre-summed into millisecond START/END pairs so ffmpeg gets
    absolute offsets, which is what player UIs need to render the chapter list."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [";FFMETADATA1"]
    if title:
        lines.append(f"title={_escape_meta(title)}")
    if artist:
        lines.append(f"artist={_escape_meta(artist)}")
    if album:
        lines.append(f"album={_escape_meta(album)}")

    cursor_ms = 0
    for ch in chapters or []:
        start = cursor_ms
        end = cursor_ms + max(0, ch.duration_ms)
        lines.extend([
            "",
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start}",
            f"END={end}",
            f"title={_escape_meta(ch.title)}",
        ])
        cursor_ms = end

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def _escape_meta(value: str) -> str:
    """ffmetadata format escapes =, ;, #, \\, and newlines with a leading backslash."""
    out = value.replace("\\", "\\\\")
    for ch in ("=", ";", "#"):
        out = out.replace(ch, "\\" + ch)
    out = out.replace("\n", "\\\n")
    return out


def _mux_m4b(
    audio_path: Path,
    out_path: Path,
    metadata_path: Path | None,
    cover_path: Path | None,
    bitrate: str,
) -> Path:
    """Run the final ffmpeg invocation that produces the .m4b file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(audio_path),
    ]
    next_input_idx = 1
    metadata_input_idx: int | None = None
    cover_input_idx: int | None = None

    if metadata_path:
        cmd += ["-i", str(metadata_path)]
        metadata_input_idx = next_input_idx
        next_input_idx += 1
    if cover_path:
        cmd += ["-i", str(cover_path)]
        cover_input_idx = next_input_idx
        next_input_idx += 1

    # Always map the audio stream from input 0.
    cmd += ["-map", "0:a"]
    if cover_input_idx is not None:
        cmd += ["-map", f"{cover_input_idx}"]
    if metadata_input_idx is not None:
        cmd += ["-map_metadata", str(metadata_input_idx)]
    if cover_input_idx is not None:
        # The cover stream needs to be marked as attached_pic so players treat
        # it as album art rather than a video stream.
        cmd += [
            "-disposition:v:0", "attached_pic",
            "-c:v", "copy",
        ]

    cmd += [
        "-c:a", "aac",
        "-b:a", bitrate,
        "-movflags", "+faststart",
        "-f", "mp4",
        str(out_path),
    ]
    _run(cmd)
    return out_path


def build_audiobook_m4b(
    audio_path: Path,
    chapters: list[Chapter],
    out_path: Path,
    title: str = "",
    artist: str = "",
    cover_image: Path | None = None,
    cover_video: Path | None = None,
    bitrate: str = "64k",
    workdir: Path | None = None,
) -> Path:
    """High-level audiobook m4b build: takes consolidated audio + chapter list
    (+ optional cover image OR cover video to extract from) and writes the .m4b.
    The chapter durations should match the audio_path exactly — caller is
    responsible for probing each chapter wav before they get cleaned up."""
    if not audio_path.exists():
        raise FileNotFoundError(f"audio not found: {audio_path}")

    workdir = workdir or out_path.parent / "_m4b_work"
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        metadata_file: Path | None = None
        if title or artist or chapters:
            metadata_file = write_ffmetadata(
                workdir / "ffmetadata.txt",
                title=title,
                artist=artist,
                album=title,
                chapters=chapters,
            )

        cover_path: Path | None = cover_image
        if cover_path is None and cover_video is not None and cover_video.exists():
            cover_path = extract_cover_from_video(cover_video, workdir / "cover.jpg")

        return _mux_m4b(
            audio_path=audio_path,
            out_path=out_path,
            metadata_path=metadata_file,
            cover_path=cover_path,
            bitrate=bitrate,
        )
    finally:
        # Clean the scratch workdir; the .m4b is self-contained.
        shutil.rmtree(workdir, ignore_errors=True)


def convert_to_m4b(
    input_audio: Path,
    out_path: Path,
    title: str = "",
    artist: str = "",
    cover_image: Path | None = None,
    bitrate: str = "64k",
) -> Path:
    """Single-file audio -> .m4b. No chapter markers (input is monolithic).
    Accepts mp3, wav, m4a, flac, ogg — anything ffmpeg can decode."""
    if not input_audio.exists():
        raise FileNotFoundError(f"input not found: {input_audio}")

    return build_audiobook_m4b(
        audio_path=input_audio,
        chapters=[],
        out_path=out_path,
        title=title,
        artist=artist,
        cover_image=cover_image,
        cover_video=None,
        bitrate=bitrate,
    )
