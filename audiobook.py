"""Audiobook orchestrator for PROJECT GAKU.

Drives the full PDF -> narrated MP4 flow by talking to the GAKU MCP server
over stdio using minimal hand-rolled JSON-RPC (no MCP client lib required).

Pipeline:
  1. extract_pdf_text         -> chapters JSON
  2. chapter cleanup -> cleaned text + pause markers
       default:      clean_chapter_with_gemini (Gemini 2.5 Flash)
       --no-gemini:  clean_chapter_local       (local Ollama model)
       --raw:        skip cleanup entirely, narrate raw extracted text
  3. generate_audio + concatenate_audio_track  -> per-chapter WAV
  4. render_visual (BookCover, ChapterCard x N)
  5. compose_final_video      -> final MP4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# Load repo-root .env into os.environ so the MCP server (Node) inherits
# GEMINI_API_KEY, KOKORO_URL, etc. when invoked from the CLI without app.py.
try:
    from dotenv import load_dotenv
    load_dotenv(HERE.parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv is optional for CLI users who set env vars manually

MCP_SERVER_CMD = ["npm", "run", "--silent", "dev", "--prefix", str(HERE / "packages" / "mcp-server")]

# Helper alias so the --export-m4b branch in main() reads naturally. The
# import here is lazy-friendly: if m4b_export fails to import (e.g. user
# moved the file), the audiobook pipeline still works for non-m4b runs.
try:
    from m4b_export import probe_duration_ms as m4b_probe
except ImportError:
    m4b_probe = None  # type: ignore

PAUSE_DURATIONS_MS = {
    "PAUSE_SHORT": 300,
    "PAUSE_LONG": 800,
    "CHAPTER_START": 1000,
    "CHAPTER_END": 1000,
}
PAUSE_TOKEN_RE = re.compile(r"\[(PAUSE_SHORT|PAUSE_LONG|CHAPTER_START|CHAPTER_END)\]")


class MCPClient:
    """Spawn the GAKU MCP server and speak JSON-RPC over its stdio."""

    def __init__(self, cmd: list[str]) -> None:
        # Force UTF-8 on the stdio pipes. The MCP server (Node) writes UTF-8;
        # Python's default Windows decoding (cp1252) chokes on smart quotes,
        # em-dashes, and other characters Gemini regularly produces.
        #
        # On Windows we go through cmd.exe (shell=True) because `npm` is a .cmd
        # file; that means terminate() would only signal the shell, not the
        # npm/node grandchildren. Put the child into its own process group so
        # close() can kill the whole tree.
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

    def _send(self, payload: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        line = json.dumps(payload) + "\n"
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def _recv(self, expect_id: int) -> dict[str, Any]:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == expect_id:
                return msg
        raise RuntimeError("MCP server closed stdout before responding.")

    def _initialize(self) -> None:
        req_id = self._next_id()
        self._send({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "gaku-audiobook", "version": "0.1.0"},
            },
        })
        self._recv(req_id)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        req_id = self._next_id()
        self._send({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        resp = self._recv(req_id)
        if "error" in resp:
            raise RuntimeError(f"MCP tool {name} failed: {resp['error']}")
        content = resp.get("result", {}).get("content", [])
        if not content:
            return None
        text = content[0].get("text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def close(self) -> None:
        """Tear the MCP server child process tree down so the orchestrator can
        exit cleanly.

        On Windows the child was spawned via cmd.exe (shell=True) and uses
        npm.cmd, which in turn spawns node.exe — `terminate()` alone only
        signals the shell, leaving npm/node as orphans. Use `taskkill /T` to
        end the whole tree. Reap with `wait()` so Python doesn't leak the
        handle, and join the stderr drain thread so the interpreter can exit.
        """
        if self._closed:
            return
        self._closed = True

        # Close stdin first so the server sees EOF on its JSON-RPC transport
        # and shuts down its stdio handler gracefully.
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except Exception:
            pass

        # Give the server a brief chance to exit on its own after EOF.
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

        if self.proc.poll() is None:
            # Still alive — force-kill the whole tree.
            if sys.platform == "win32":
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                        capture_output=True,
                        check=False,
                        timeout=5,
                    )
                except Exception:
                    pass
            else:
                try:
                    os.killpg(os.getpgid(self.proc.pid), 15)  # SIGTERM
                except Exception:
                    pass
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(self.proc.pid), 9)  # SIGKILL
                    except Exception:
                        pass

        try:
            self.proc.wait(timeout=3)
        except Exception:
            pass

        # Close the remaining pipes so the drain thread's read loop returns.
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                if stream and not stream.closed:
                    stream.close()
            except Exception:
                pass

        # Join the stderr-drain thread so it doesn't keep a reference alive
        # past interpreter shutdown. It's a daemon, so failure to join is
        # non-fatal — but a clean join makes shutdown deterministic.
        try:
            self._stderr_thread.join(timeout=2)
        except Exception:
            pass


def split_on_pause_markers(text: str) -> list[dict[str, Any]]:
    """Turn cleaned chapter text into an ordered list of segments:
        [{"kind": "text", "text": "..."},
         {"kind": "pause", "ms": 300}, ...]
    Strips leading/trailing whitespace from text segments and drops empties."""
    segments: list[dict[str, Any]] = []
    pos = 0
    for m in PAUSE_TOKEN_RE.finditer(text):
        chunk = text[pos:m.start()].strip()
        if chunk:
            segments.append({"kind": "text", "text": chunk})
        segments.append({"kind": "pause", "ms": PAUSE_DURATIONS_MS[m.group(1)]})
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        segments.append({"kind": "text", "text": tail})
    return segments


def synth_segments(
    client: MCPClient,
    segments: list[dict[str, Any]],
    voice: str,
    out_dir: Path,
    chapter_num: int,
) -> list[dict[str, Any]]:
    """Synthesize each text segment via generate_audio, returning a clip list
    shaped for concatenate_audio_track. Pause segments map to empty clips with
    a pause_after_ms gap."""
    clips: list[dict[str, Any]] = []
    text_idx = 0
    for seg in segments:
        if seg["kind"] == "pause":
            if clips:
                clips[-1]["pause_after_ms"] = clips[-1].get("pause_after_ms", 0) + seg["ms"]
            else:
                clips.append({
                    "line_id": f"ch{chapter_num:02d}_pause_lead",
                    "host_id": "A",
                    "path": "",
                    "duration_ms": 0,
                    "pause_after_ms": seg["ms"],
                })
            continue

        text_idx += 1
        line_id = f"ch{chapter_num:02d}_l{text_idx:03d}"
        # generate_audio wants an Episode shape. Build a minimal one with a single line.
        episode = {
            "episode_id": f"audiobook-ch{chapter_num:02d}-{line_id}",
            "topic": f"Chapter {chapter_num}",
            "target_duration_minutes": 5,
            "hosts": [
                {"id": "A", "name": "Narrator", "voice_id": voice},
                {"id": "B", "name": "Narrator2", "voice_id": voice},
            ],
            "segments": [{
                "segment_id": "s01",
                "title": "narration",
                "lines": [{"line_id": line_id, "host_id": "A", "text": seg["text"], "pause_after_ms": 0}],
            }],
        }
        result = client.call("generate_audio", {
            "episode": episode,
            "out_dir": str(out_dir),
        })
        clip = result["clips"][0]
        clip["pause_after_ms"] = 0
        clips.append(clip)

    # Drop any leading empty-path "pause-only" lead clip — concat can't ingest it.
    clips = [c for c in clips if c.get("path")]
    return clips


def render_chapter_cards(
    client: MCPClient,
    chapters: list[dict[str, Any]],
    out_dir: Path,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for ch in chapters:
        out_path = out_dir / f"chapter_{ch['chapter_num']:02d}_card.mp4"
        client.call("render_visual", {
            "template": "ChapterCard",
            "data": {
                "chapter_num": ch["chapter_num"],
                "chapter_title": ch["title"],
            },
            "duration_ms": 3000,
            "out_path": str(out_path),
        })
        cards.append({"chapter_num": ch["chapter_num"], "path": str(out_path)})
    return cards


def main() -> int:
    parser = argparse.ArgumentParser(description="PDF -> narrated MP4 audiobook pipeline.")
    parser.add_argument("--pdf", required=True, help="Path to source PDF")
    parser.add_argument(
        "--no-gemini",
        action="store_true",
        help="Use a local Ollama model for chapter cleanup instead of Gemini (no API key required)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Skip all AI cleanup and narrate raw extracted text directly (fastest, lowest quality)",
    )
    parser.add_argument("--voice", default="af_bella", help="Kokoro voice ID (default: af_bella)")
    parser.add_argument("--out", default="output/audiobook/", help="Output directory")
    parser.add_argument(
        "--chapters",
        default=None,
        help="Comma-separated chapter numbers to process (e.g. '1,2,3') — useful for testing",
    )
    parser.add_argument(
        "--no-chapters",
        action="store_true",
        help="Bypass PDF heading detection. Treat the book as continuous text and chunk by --chunk-pages instead. Use this when the chapter heuristic over-segments the PDF (e.g. detects 1000s of false chapters).",
    )
    parser.add_argument(
        "--chunk-pages",
        type=int,
        default=10,
        help="When --no-chapters is set, group every N consecutive pages into one chunk for AI cleanup + audio (default: 10).",
    )
    parser.add_argument(
        "--tone",
        default="Educational",
        help="Conversational | Educational | Storytelling | Technical | Inspirational (passed to the Gemini cleanup prompt).",
    )
    parser.add_argument(
        "--audience-level",
        default="Intermediate",
        help="Beginner | Intermediate | Advanced | Expert (passed to the Gemini cleanup prompt).",
    )
    parser.add_argument(
        "--focus",
        default="",
        help="Free-form focus directive forwarded to the Gemini cleanup prompt.",
    )
    parser.add_argument(
        "--keep-intermediates",
        action="store_true",
        help="Keep per-line WAV fragments and per-chapter intermediate WAVs after the final audiobook is written. By default these are deleted to avoid clutter.",
    )
    parser.add_argument(
        "--export-m4b",
        action="store_true",
        help="Also produce an audiobook.m4b alongside the WAV/MP4. Includes chapter markers and embedded cover art.",
    )
    parser.add_argument(
        "--m4b-bitrate",
        default="64k",
        help="AAC bitrate for the m4b (default: 64k — audiobook sweet spot for spoken word).",
    )
    parser.add_argument(
        "--m4b-author",
        default="",
        help="Author / artist tag to embed in the m4b metadata (default: empty).",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    out_dir = Path(args.out).resolve()
    audio_dir = out_dir / "audio"
    visuals_dir = out_dir / "visuals"
    audio_dir.mkdir(parents=True, exist_ok=True)
    visuals_dir.mkdir(parents=True, exist_ok=True)

    chapter_filter: set[int] | None = None
    if args.chapters:
        chapter_filter = {int(x.strip()) for x in args.chapters.split(",") if x.strip()}

    print(f"[audiobook] spawning MCP server: {' '.join(MCP_SERVER_CMD)}", file=sys.stderr)
    client = MCPClient(MCP_SERVER_CMD)

    try:
        print("[audiobook] extracting PDF…", file=sys.stderr)
        extract_args: dict[str, Any] = {"pdf_path": str(pdf_path)}
        if args.no_chapters:
            extract_args["no_chapters"] = True
            extract_args["chunk_pages"] = args.chunk_pages
            print(
                f"[audiobook] no-chapters mode: chunking every {args.chunk_pages} pages",
                file=sys.stderr,
            )
        extracted = client.call("extract_pdf_text", extract_args)
        chapters = extracted["chapters"]
        print(
            f"[audiobook] found {extracted['total_chapters']} chapters, "
            f"~{extracted['estimated_duration_minutes']} min total",
            file=sys.stderr,
        )

        if chapter_filter is not None:
            chapters = [c for c in chapters if c["chapter_num"] in chapter_filter]
            print(f"[audiobook] filtered to chapters: {sorted(chapter_filter)}", file=sys.stderr)

        # 2 + 3: per-chapter cleanup, TTS, concat.
        chapter_audio: list[dict[str, Any]] = []
        for ch in chapters:
            num = ch["chapter_num"]
            print(f"[audiobook] chapter {num}: {ch['title']}", file=sys.stderr)

            if args.raw:
                cleaned_text = f"[CHAPTER_START]\nChapter {num}. {ch['title']}.\n[PAUSE_LONG]\n{ch['text']}\n[CHAPTER_END]"
                pause_markers = 4
            elif args.no_gemini:
                resp = client.call("clean_chapter_local", {
                    "chapter_text": ch["text"],
                    "chapter_title": ch["title"],
                    "chapter_num": num,
                })
                cleaned_text = resp["cleaned_text"]
                pause_markers = resp.get("pause_markers", 0)
                if resp.get("ollama_failed"):
                    print(f"[audiobook]   ollama failed: {resp.get('warning')}", file=sys.stderr)
            else:
                resp = client.call("clean_chapter_with_gemini", {
                    "chapter_text": ch["text"],
                    "chapter_title": ch["title"],
                    "chapter_num": num,
                    "tone": args.tone,
                    "audience_level": args.audience_level,
                    "focus": args.focus,
                })
                cleaned_text = resp["cleaned_text"]
                pause_markers = resp.get("pause_markers", 0)
                if resp.get("gemini_failed"):
                    print(f"[audiobook]   gemini failed: {resp.get('warning')}", file=sys.stderr)
            print(f"[audiobook]   pause markers: {pause_markers}", file=sys.stderr)

            segments = split_on_pause_markers(cleaned_text)
            ch_audio_dir = audio_dir / f"chapter_{num:02d}"
            ch_audio_dir.mkdir(parents=True, exist_ok=True)
            clips = synth_segments(client, segments, args.voice, ch_audio_dir, num)

            chapter_wav = audio_dir / f"chapter_{num:02d}.wav"
            client.call("concatenate_audio_track", {
                "clips": clips,
                "out_path": str(chapter_wav),
            })
            chapter_audio.append({
                "chapter_num": num,
                "path": str(chapter_wav),
                "title": ch["title"],
            })

            # Clean up the per-line WAV fragments now that the chapter WAV is finalized.
            if not args.keep_intermediates and ch_audio_dir.exists():
                shutil.rmtree(ch_audio_dir, ignore_errors=True)

        # 4. Book cover.
        print("[audiobook] rendering book cover…", file=sys.stderr)
        cover_path = visuals_dir / "book_cover.mp4"
        client.call("render_visual", {
            "template": "BookCover",
            "data": {"title": extracted.get("title") or pdf_path.stem, "author": ""},
            "duration_ms": 5000,
            "out_path": str(cover_path),
        })

        # 4. Chapter cards.
        print("[audiobook] rendering chapter cards…", file=sys.stderr)
        cards = render_chapter_cards(client, chapters, visuals_dir)

        # 5. Final compose: cover -> chapter card (held for actual audio duration) per chapter.
        print("[audiobook] composing final video…", file=sys.stderr)

        def wav_duration_ms(path: Path) -> int:
            """Return WAV duration in milliseconds using stdlib wave module."""
            try:
                with wave.open(str(path), "rb") as wf:
                    return int(wf.getnframes() / wf.getframerate() * 1000)
            except Exception:
                return 60_000  # fallback: 1 minute if probe fails

        visuals_payload = [{"path": str(cover_path), "duration_ms": 5000}]
        for ch_audio, card in zip(chapter_audio, cards):
            # Use actual WAV duration so the chapter card stays on screen for
            # the full chapter. Do NOT add the WAV path itself — it has no
            # video stream and would crash FFmpeg's filtergraph.
            duration = wav_duration_ms(Path(ch_audio["path"]))
            visuals_payload.append({
                "path": card["path"],
                "duration_ms": duration,
            })

        final_path = out_dir / "audiobook.mp4"
        # compose_final_video expects an audio_path + visuals; for an audiobook we
        # concatenate per-chapter audio into one track first.
        full_audio = audio_dir / "audiobook_full.wav"
        client.call("concatenate_audio_track", {
            "clips": [
                {"line_id": f"ch{c['chapter_num']:02d}", "path": c["path"], "pause_after_ms": 0}
                for c in chapter_audio
            ],
            "out_path": str(full_audio),
        })

        client.call("compose_final_video", {
            "audio_path": str(full_audio),
            "visuals": visuals_payload,
            "out_path": str(final_path),
        })

        # M4B export: probe each per-chapter WAV duration BEFORE the cleanup
        # pass deletes them, then mux with chapter markers + cover art.
        if args.export_m4b:
            from m4b_export import Chapter, build_audiobook_m4b
            print("[audiobook] building m4b…", file=sys.stderr)
            m4b_chapters: list[Chapter] = []
            for c in chapter_audio:
                wav = Path(c["path"])
                if not wav.exists():
                    print(f"[audiobook]   skip missing {wav}", file=sys.stderr)
                    continue
                try:
                    dur = m4b_probe(wav)
                except Exception as exc:
                    print(f"[audiobook]   ffprobe failed on {wav}: {exc}", file=sys.stderr)
                    continue
                m4b_chapters.append(Chapter(title=c.get("title") or f"Chapter {c['chapter_num']}", duration_ms=dur))
            try:
                m4b_path = out_dir / "audiobook.m4b"
                build_audiobook_m4b(
                    audio_path=full_audio,
                    chapters=m4b_chapters,
                    out_path=m4b_path,
                    title=extracted.get("title") or pdf_path.stem,
                    artist=args.m4b_author,
                    cover_video=cover_path if cover_path.exists() else None,
                    bitrate=args.m4b_bitrate,
                )
                print(f"[audiobook] m4b written: {m4b_path}", file=sys.stderr)
            except Exception as exc:
                print(f"[audiobook] m4b export FAILED: {exc}", file=sys.stderr)

        # Once the final MP4 + consolidated WAV exist, drop the per-chapter
        # WAVs that fed into them. Keep audiobook_full.wav for re-export.
        if not args.keep_intermediates:
            for c in chapter_audio:
                p = Path(c["path"])
                if p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        pass

        print(f"\n[audiobook] DONE: {final_path}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
