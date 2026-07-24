"""compose_only.py — Resume an audiobook pipeline run from the compose step.

Run this when audiobook.py completed all chapters and audio but crashed during
compose_final_video or m4b export.  It reuses all existing WAVs and chapter
cards without re-generating audio or visuals.

Usage (from PROJECT GAKU root):
    python podcast-pipeline/compose_only.py --out output/audiobook/ --m4b-author "Malcolm Gladwell" --title "The Tipping Point"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
    load_dotenv(HERE.parent / ".env", override=False)
except ImportError:
    pass

MCP_SERVER_CMD = ["npm", "run", "--silent", "dev", "--prefix", str(HERE / "packages" / "mcp-server")]


class MCPClient:
    def __init__(self, cmd: list[str]) -> None:
        popen_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=sys.platform == "win32",
            **popen_kwargs,
        )
        self._id = 0
        self._closed = False
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        self._initialize()

    def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            sys.stderr.write(f"[mcp] {line}")

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _initialize(self) -> None:
        req = {"jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
               "params": {"protocolVersion": "2024-11-05",
                          "capabilities": {},
                          "clientInfo": {"name": "compose-only", "version": "1.0"}}}
        self._send(req)
        self._recv()
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def _send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        line = json.dumps(obj) + "\n"
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def _recv(self) -> dict:
        assert self.proc.stdout is not None
        while True:
            raw = self.proc.stdout.readline()
            if not raw:
                raise RuntimeError("MCP server closed stdout unexpectedly")
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "id" in obj:
                return obj

    def call(self, name: str, args: dict) -> Any:
        req = {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
               "params": {"name": name, "arguments": args}}
        self._send(req)
        resp = self._recv()
        if "error" in resp:
            raise RuntimeError(f"MCP tool {name} failed: {resp['error']}")
        content = resp.get("result", {}).get("content", [])
        for item in content:
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except Exception:
                    return item["text"]
        return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass


def wav_duration_ms(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as wf:
            return int(wf.getnframes() / wf.getframerate() * 1000)
    except Exception:
        return 60_000


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume audiobook from compose step.")
    parser.add_argument("--out", default="output/audiobook/", help="Output directory (must contain audio/ and visuals/)")
    parser.add_argument("--m4b-author", default="", help="Author tag for m4b metadata")
    parser.add_argument("--title", default="Audiobook", help="Title tag for m4b metadata")
    parser.add_argument("--m4b-bitrate", default="64k", help="AAC bitrate for m4b (default: 64k)")
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    audio_dir = out_dir / "audio"
    visuals_dir = out_dir / "visuals"
    full_audio = audio_dir / "audiobook_full.wav"
    final_path = out_dir / "audiobook.mp4"
    m4b_path = out_dir / "audiobook.m4b"

    if not full_audio.exists():
        print(f"[compose] ERROR: full audio not found at {full_audio}", file=sys.stderr)
        print("[compose] Re-run the full audiobook.py pipeline — the audio wasn't generated.", file=sys.stderr)
        return 1

    # Collect chapter WAVs and chapter cards in sorted order
    chapter_wavs = sorted(audio_dir.glob("chapter_*.wav"))
    chapter_cards = sorted(visuals_dir.glob("chapter_*_card.mp4"))
    cover = visuals_dir / "book_cover.mp4"

    print(f"[compose] Found {len(chapter_wavs)} chapter WAVs, {len(chapter_cards)} chapter cards", file=sys.stderr)

    if not chapter_cards:
        print("[compose] ERROR: no chapter cards found in visuals/", file=sys.stderr)
        return 1

    if not cover.exists():
        print("[compose] WARNING: book_cover.mp4 not found, skipping cover", file=sys.stderr)

    # Build visuals payload: cover + one card per chapter, held for actual WAV duration
    visuals_payload = []
    if cover.exists():
        visuals_payload.append({"path": str(cover), "duration_ms": 5000})

    for card, wav in zip(chapter_cards, chapter_wavs):
        duration = wav_duration_ms(wav)
        visuals_payload.append({"path": str(card), "duration_ms": duration})
        print(f"[compose]   {card.name} → {duration/1000:.1f}s", file=sys.stderr)

    # If more cards than wavs (or vice versa), handle gracefully
    extra_cards = chapter_cards[len(chapter_wavs):]
    for card in extra_cards:
        visuals_payload.append({"path": str(card), "duration_ms": 60_000})

    print(f"[compose] Spawning MCP server…", file=sys.stderr)
    client = MCPClient(MCP_SERVER_CMD)

    try:
        print(f"[compose] Composing final video ({len(visuals_payload)} visual clips)…", file=sys.stderr)
        client.call("compose_final_video", {
            "audio_path": str(full_audio),
            "visuals": visuals_payload,
            "out_path": str(final_path),
        })
        print(f"[compose] ✓ MP4 written → {final_path}", file=sys.stderr)

        # M4B export
        print("[compose] Building m4b…", file=sys.stderr)
        sys.path.insert(0, str(HERE))
        from m4b_export import Chapter, build_audiobook_m4b

        # Chapter() takes the per-chapter LENGTH (duration_ms); build_audiobook_m4b
        # accumulates these into absolute start/end offsets internally.
        m4b_chapters: list[Chapter] = []
        for i, wav in enumerate(chapter_wavs):
            m4b_chapters.append(Chapter(
                title=f"Chapter {i + 1}",
                duration_ms=wav_duration_ms(wav),
            ))

        # Prefer a still image if one exists; otherwise let build_audiobook_m4b
        # pull a frame out of the cover video (book_cover.mp4).
        cover_png = visuals_dir / "book_cover.png"
        cover_image_arg = cover_png if cover_png.exists() else None
        cover_video_arg = cover if (cover.exists() and cover_image_arg is None) else None

        build_audiobook_m4b(
            audio_path=full_audio,
            out_path=m4b_path,
            chapters=m4b_chapters,
            title=args.title,
            artist=args.m4b_author,
            cover_image=cover_image_arg,
            cover_video=cover_video_arg,
            bitrate=args.m4b_bitrate,
        )
        print(f"[compose] ✓ M4B written → {m4b_path}", file=sys.stderr)

    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
