"""Research-to-podcast orchestrator for PROJECT GAKU.

Chains:
  1. gaku.py             -> scraped sources for a topic (results.md)
  2. synthesize_research_to_script (MCP, Gemini)  -> validated episode JSON
  3. generate_audio (MCP, Kokoro TTS)              -> per-line WAVs
  4. concatenate_audio_track (MCP, ffmpeg)         -> single podcast WAV

Reuses the same MCPClient and PYTHONIOENCODING fixes as audiobook.py.

Usage:
  python research_podcast.py --topic "Quantum Computing" --out output/podcasts
  python research_podcast.py --research-md results.md --topic "Quantum Computing"

Kokoro autostop: a run that synthesizes with Kokoro stops the Docker container
named `kokoro` on the way out (success, error, or Ctrl+C). Disable with
--no-kokoro-autostop or GAKU_KOKORO_AUTOSTOP=0/false/no/off. Runs that never
use Kokoro do not stop it.

# YouTube upload: place youtube_client_secrets.json in this directory.
# On first run it will open a browser for OAuth approval.
# Videos upload as private — review before publishing.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Kokoro voice pool grouped by gender. Used by _pick_voices() when the user
# doesn't pin --voice-a / --voice-b. Names match kokoro-fastapi's voice ids.
# Blocklisted voices (config/voice_blocklist.json, e.g. Adam) are filtered out.
from voice_policy import enforce_episode_hosts, enforce_voice, filter_voices  # noqa: E402

MALE_VOICES = filter_voices(["bm_george", "bm_lewis", "am_michael"])
FEMALE_VOICES = filter_voices(["bf_emma", "bf_v0isabella", "af_bella", "af_sarah"])
# Probability that a random pairing is mixed-gender (male + female) vs.
# same-gender. Mixed sounds more like a typical co-hosted podcast.
MIXED_GENDER_PROBABILITY = 0.75


def _pick_voices(rng: random.Random | None = None) -> tuple[str, str]:
    """Pick two distinct Kokoro voices for Host A / Host B.

    75% of the time the pair is mixed-gender (one male + one female); the
    remaining 25% is same-gender for variety. Never returns the same voice
    twice. Pass `rng` to make selection deterministic in tests.
    """
    r = rng or random
    if r.random() < MIXED_GENDER_PROBABILITY:
        male = r.choice(MALE_VOICES)
        female = r.choice(FEMALE_VOICES)
        # Randomize who's primary so we don't always lead with the male voice.
        return (male, female) if r.random() < 0.5 else (female, male)
    pool = MALE_VOICES if r.random() < 0.5 else FEMALE_VOICES
    a, b = r.sample(pool, 2)
    return a, b

# Reuse the MCPClient from audiobook.py to avoid duplication and inherit the
# UTF-8 stdio fix.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from audiobook import MCPClient, MCP_SERVER_CMD  # noqa: E402
import kokoro_lifecycle  # noqa: E402

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
GAKU_SCRIPT = REPO_ROOT / "gaku.py"
DEFAULT_RESULTS = REPO_ROOT / "results.md"

# Google Drive File Stream sync target. Folder name has TWO spaces between
# "PROJECT" and "学" — a single-space path will silently miss the folder.
DRIVE_SYNC_DIR = Path(r"D:\My Drive\PROJECT  学")


def slugify_title(title: str, max_len: int = 60) -> str:
    """Filesystem-safe slug. Strips weird chars, collapses runs of separators,
    truncates to max_len. Falls back to 'podcast' for empty input."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", title or "").strip("._-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("._-")
    return s or "podcast"


def make_run_dir(out_parent: Path, topic: str) -> Path:
    """Build a unique per-run directory: <parent>/<dd.mm.yy>_<slug>/.
    If a same-day run with the same topic already exists, append _2, _3, ..."""
    date_str = datetime.now().strftime("%d.%m.%y")
    slug = slugify_title(topic)
    base = out_parent / f"{date_str}_{slug}"
    if not base.exists():
        return base
    n = 2
    while True:
        candidate = out_parent / f"{date_str}_{slug}_{n}"
        if not candidate.exists():
            return candidate
        n += 1


def run_gaku(topic: str) -> Path:
    """Spawn gaku.py for the topic; return the path to results.md it produced."""
    print(f"[podcast] running gaku.py for topic: {topic!r}", file=sys.stderr)
    proc = subprocess.run(
        [sys.executable, str(GAKU_SCRIPT), "--topic", topic],
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or proc.stdout or "")
        raise SystemExit(f"gaku.py failed (exit {proc.returncode})")
    if not DEFAULT_RESULTS.exists():
        raise SystemExit("gaku.py finished but results.md was not produced")
    return DEFAULT_RESULTS


def _strip_episode_markdown(episode: dict[str, Any], where: str) -> int:
    """Remove markdown emphasis (*x*, **x**, _x_, `x`) from dialogue + card text
    so neither TTS nor karaoke sees it. Words are kept, so alignment is 1:1."""
    try:
        from text_clean import clean_episode_markdown

        n = clean_episode_markdown(episode)
    except Exception as exc:  # never fatal
        print(f"[podcast] warning: markdown strip failed ({where}): {exc!r}", file=sys.stderr)
        return 0
    if n:
        print(f"[podcast] stripped markdown emphasis from {n} string(s) ({where})", file=sys.stderr)
    return n


def _extract_pdf_to_md(pdf_path: Path, out_parent: Path) -> Path:
    """Extract an article PDF's text into <out>/_source_pdfs/<stem>.md.

    Keeps it simple (page text in reading order, de-hyphenated, page numbers
    and repeated running headers dropped) so Gemini gets the full article as
    research notes and fact-check gets it as the primary source."""
    import fitz  # pymupdf

    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    doc = fitz.open(str(pdf_path))
    pages = [page.get_text("text") for page in doc]
    doc.close()
    line_counts: dict[str, int] = {}
    for txt in pages:
        for ln in {l.strip() for l in txt.splitlines() if l.strip()}:
            line_counts[ln] = line_counts.get(ln, 0) + 1
    repeated = {ln for ln, c in line_counts.items() if c >= max(3, len(pages) // 2) and len(ln) < 90}
    out_lines: list[str] = []
    for txt in pages:
        for ln in txt.splitlines():
            t = ln.strip()
            if not t or t in repeated or re.fullmatch(r"\d{1,4}", t):
                if out_lines and out_lines[-1]:
                    out_lines.append("")
                continue
            if out_lines and out_lines[-1].endswith("-") and t[:1].islower():
                out_lines[-1] = out_lines[-1][:-1] + t
            else:
                out_lines.append(t)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
    if len(body) < 500:
        raise ValueError(f"PDF text too short ({len(body)} chars) -- scanned PDF?")
    dest_dir = out_parent / "_source_pdfs"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (re.sub(r"[^\w.-]+", "_", pdf_path.stem)[:80] + ".md")
    dest.write_text(f"# Source: {pdf_path.name}\n\n{body}\n", encoding="utf-8")
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Topic -> scraped sources -> Gemini script -> narrated podcast audio."
    )
    parser.add_argument("--topic", required=True, help="Podcast topic (used by gaku.py and as the Gemini TOPIC field)")
    parser.add_argument(
        "--research-md",
        default=None,
        help="Pre-existing markdown research notes. If omitted, gaku.py is run for --topic.",
    )
    parser.add_argument(
        "--episode-in",
        default=None,
        help="Path to an existing episode.json. Skips research + Gemini synthesis and "
             "writes the run into the same parent dir as the episode.json file. Audio is still "
             "regenerated (Kokoro is local + fast). Use to re-render video without paying for Gemini.",
    )
    parser.add_argument("--out", default="output/podcasts", help="Output directory")
    parser.add_argument(
        "--duration",
        type=int,
        default=8,
        help="Target podcast duration in minutes (default: 8)",
    )
    parser.add_argument(
        "--voice-a",
        default=None,
        help="Kokoro voice for Host A. If omitted, a random voice is picked per run.",
    )
    parser.add_argument(
        "--voice-b",
        default=None,
        help="Kokoro voice for Host B. If omitted, a random voice is picked per run "
             "(mixed-gender with --voice-a 75%% of the time).",
    )
    parser.add_argument(
        "--tone",
        default="Conversational",
        help="Conversational | Educational | Storytelling | Technical | Inspirational",
    )
    parser.add_argument(
        "--audience-level",
        default="Intermediate",
        help="Beginner | Intermediate | Advanced | Expert",
    )
    parser.add_argument(
        "--focus",
        default="",
        help="Free-form focus directive appended to the Gemini prompt.",
    )
    parser.add_argument(
        "--episode-out",
        default=None,
        help="Optional path to also write the raw episode JSON (for debugging / re-runs)",
    )
    parser.add_argument(
        "--keep-intermediates",
        action="store_true",
        help="Keep the per-line WAV fragments in <out>/audio after the final podcast WAV is written. By default they are deleted to avoid clutter.",
    )
    parser.add_argument(
        "--mp3-bitrate",
        default="64k",
        help="MP3 bitrate for the compressed final (default: 64k — audiobook sweet spot for spoken word).",
    )
    parser.add_argument(
        "--keep-wav",
        action="store_true",
        help="Keep the uncompressed podcast.wav alongside podcast.mp3. By default the WAV is deleted after MP3 conversion to save disk space (~5× smaller).",
    )
    parser.add_argument(
        "--video",
        action="store_true",
        help="Also produce podcast.mp4 with scripted visuals overlaid on a looped background. Adds ~1-2 min per visual to total runtime. Off by default — audio-only is the fast path.",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Keep outputs local: skip YouTube upload and Drive copy",
    )
    parser.add_argument(
        "--no-drive",
        action="store_true",
        help="Skip Drive copy (also disabled by --no-upload or GAKU_NO_DRIVE=1)",
    )
    parser.add_argument(
        "--upload-privacy",
        choices=("private", "unlisted", "public"),
        default="private",
        help="YouTube privacy when uploading (default: private). Use public only when explicitly authorized.",
    )
    parser.add_argument(
        "--yt-title",
        default=None,
        help="Optional YouTube title override (max 100 chars).",
    )
    parser.add_argument(
        "--series",
        default=None,
        help="YouTube series key from config/playlist_series.json (e.g. hbr). "
             "Auto-detected from HBR paths/metadata when omitted.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Primary-source text/markdown for fact-check (HBR / framework listens). "
             "Also auto-detects hbr_source.md / *_source.txt in the run dir.",
    )
    parser.add_argument(
        "--source-pdf",
        default=None,
        help="HBR/article PDF. Text is extracted (pymupdf) to hbr_source.md and used as "
             "--research-md and --source when those are not given.",
    )
    parser.add_argument(
        "--skip-factcheck",
        action="store_true",
        help="Skip the primary-source fact-check step even when a source file is present.",
    )
    parser.add_argument(
        "--no-kokoro-autostop",
        action="store_true",
        help="Leave the Kokoro Docker container running when this build finishes. "
             "By default the container named kokoro is stopped after a run that used "
             "Kokoro (also disabled by GAKU_KOKORO_AUTOSTOP=0/false/no/off).",
    )
    args = parser.parse_args()

    source_pdf_md: Path | None = None
    if args.source_pdf and not args.episode_in:
        try:
            source_pdf_md = _extract_pdf_to_md(Path(args.source_pdf).resolve(), Path(args.out).resolve())
        except Exception as exc:
            print(f"--source-pdf extraction failed: {exc!r}", file=sys.stderr)
            return 2
        if not args.research_md:
            args.research_md = str(source_pdf_md)
        if not args.source:
            args.source = str(source_pdf_md)
        print(f"[podcast] source PDF -> {source_pdf_md}", file=sys.stderr)

    # Resolve voices. If either side wasn't pinned on the CLI, pick a random
    # pair (mixed-gender 75% of the time). If only one side was pinned, fill
    # the other with a different random voice so we never collide.
    if args.voice_a is None and args.voice_b is None:
        args.voice_a, args.voice_b = _pick_voices()
    elif args.voice_a is None:
        pool = [v for v in (MALE_VOICES + FEMALE_VOICES) if v != args.voice_b]
        args.voice_a = random.choice(pool)
    elif args.voice_b is None:
        pool = [v for v in (MALE_VOICES + FEMALE_VOICES) if v != args.voice_a]
        args.voice_b = random.choice(pool)
    args.voice_a = enforce_voice(args.voice_a, context="--voice-a")
    args.voice_b = enforce_voice(args.voice_b, context="--voice-b")
    if args.voice_a == args.voice_b:  # a swap can collide with the other host
        same = MALE_VOICES if str(args.voice_b)[1:2] == "m" else FEMALE_VOICES
        args.voice_b = random.choice([v for v in same if v != args.voice_a]
                                     or [v for v in (MALE_VOICES + FEMALE_VOICES) if v != args.voice_a])
    print(f"[voices] Primary: {args.voice_a} | Secondary: {args.voice_b}", file=sys.stderr)

    # --episode-in skips research + Gemini. The run dir is the parent of the
    # given episode.json (we re-use that folder, including its existing audio
    # if --reuse-audio works out). research_notes stays empty.
    if args.episode_in:
        episode_in_path = Path(args.episode_in).resolve()
        if not episode_in_path.exists():
            print(f"--episode-in file not found: {episode_in_path}", file=sys.stderr)
            return 2
        out_dir = episode_in_path.parent
        audio_dir = out_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        print(f"[podcast] run directory (from --episode-in): {out_dir}", file=sys.stderr)
        research_notes = ""
        research_path = None
    else:
        research_path = Path(args.research_md).resolve() if args.research_md else run_gaku(args.topic)
        if not research_path.exists():
            print(f"Research notes not found: {research_path}", file=sys.stderr)
            return 2

        research_notes = research_path.read_text(encoding="utf-8")
        if not research_notes.strip():
            print("Research notes are empty — nothing for Gemini to synthesize.", file=sys.stderr)
            return 2

        # Per-run subfolder: <parent>/<dd.mm.yyyy>_<safe-title>/.
        # Each run is self-contained so previous runs aren't clobbered.
        out_parent = Path(args.out).resolve()
        out_dir = make_run_dir(out_parent, args.topic)
        audio_dir = out_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        print(f"[podcast] run directory: {out_dir}", file=sys.stderr)

        # Preserve the research notes that fed this run, so each run folder is
        # self-describing without depending on the repo-root results.md (which
        # gets overwritten by the next gaku.py invocation).
        try:
            shutil.copyfile(research_path, out_dir / "results.md")
        except OSError as exc:
            print(f"[podcast] warning: failed to copy results.md: {exc}", file=sys.stderr)
        if source_pdf_md is not None:
            try:
                shutil.copyfile(source_pdf_md, out_dir / "hbr_source.md")
                args.source = str(out_dir / "hbr_source.md")
            except OSError as exc:
                print(f"[podcast] warning: failed to copy hbr_source.md: {exc}", file=sys.stderr)

    print(f"[podcast] spawning MCP server: {' '.join(MCP_SERVER_CMD)}", file=sys.stderr)
    client = MCPClient(MCP_SERVER_CMD)
    from gemini_safety import guard_mcp_call
    client.call = guard_mcp_call(client.call)

    try:
        if args.episode_in:
            episode = json.loads(Path(args.episode_in).read_text(encoding="utf-8"))
            print(f"[podcast] loaded episode from {args.episode_in}", file=sys.stderr)
        else:
            print("[podcast] synthesizing script with Gemini…", file=sys.stderr)
            resp = client.call("synthesize_research_to_script", {
                "topic": args.topic,
                "research_notes": research_notes,
                "target_duration_minutes": args.duration,
                "voice_a": args.voice_a,
                "voice_b": args.voice_b,
                "tone": args.tone,
                "audience_level": args.audience_level,
                "focus": args.focus,
            })
            if not isinstance(resp, dict):
                print(f"Unexpected synthesizer response shape: {type(resp).__name__}", file=sys.stderr)
                return 3
            if resp.get("gemini_failed"):
                print(f"[podcast] Gemini synthesis failed: {resp.get('warning')}", file=sys.stderr)
                return 3
            episode = resp.get("episode")
            if not episode:
                print("[podcast] Gemini returned no episode object.", file=sys.stderr)
                print(f"  raw response prefix: {(resp.get('raw_response') or '')[:200]!r}", file=sys.stderr)
                return 3

            _strip_episode_markdown(episode, "script")

            # 2026-09-28 (item 3): don't retell the cold-open hook in the next
            # segment. Detect heavy overlap; rewrite only those lines (Gemini,
            # model fallback). Never fatal; report saved next to episode.json.
            try:
                from script_dedupe import dedupe_hook_retell
                episode, dd_report = dedupe_hook_retell(episode, research_notes)
                (out_dir / "script_dedupe_report.json").write_text(
                    json.dumps(dd_report, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                if dd_report.get("heavy"):
                    print(
                        f"[podcast] hook-retell dedupe: action={dd_report.get('action')} "
                        f"rewritten={dd_report.get('rewritten')} in {dd_report.get('next_segment')}",
                        file=sys.stderr,
                    )
            except Exception as exc:
                print(f"[podcast] warning: hook-retell dedupe failed: {exc!r}", file=sys.stderr)

            # Always save the Gemini-generated episode script into the run folder
            # so the run is fully reproducible from disk. --episode-out, if set,
            # additionally writes to a user-specified path.
            default_episode_path = out_dir / "episode.json"
            try:
                default_episode_path.write_text(
                    json.dumps(episode, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                print(f"[podcast] wrote episode JSON: {default_episode_path}", file=sys.stderr)
            except OSError as exc:
                print(f"[podcast] warning: failed to write episode.json: {exc}", file=sys.stderr)
            if args.episode_out:
                Path(args.episode_out).write_text(json.dumps(episode, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"[podcast] wrote episode JSON: {args.episode_out}", file=sys.stderr)

        # Best-effort validation. A schema failure is loud but non-fatal — we
        # still try TTS, since a slightly-off episode often plays fine.
        try:
            validation = client.call("validate_episode_json", {"episode": episode})
            if isinstance(validation, dict) and not validation.get("valid", True):
                errs = validation.get("errors") or []
                print(f"[podcast] WARNING: episode JSON failed schema validation: {errs}", file=sys.stderr)
        except Exception as exc:
            print(f"[podcast] validator threw {exc!r} — continuing anyway", file=sys.stderr)

        # Keep host display names in sync with the Kokoro voice_ids that were
        # actually picked (voice_persona), not Gemini's placeholder Alex/Sam.
        try:
            enforce_episode_hosts(episode, context="episode.json")
            from visual_queue import voice_persona as _voice_persona
            for h in episode.get("hosts") or []:
                vid = (h.get("voice_id") or "").strip()
                if vid:
                    persona = _voice_persona(vid)
                    h["name"] = persona["name"]
                    h["color"] = persona["color"]
            # Persist patched hosts back to episode.json when present.
            ep_path = out_dir / "episode.json"
            if ep_path.exists():
                ep_path.write_text(json.dumps(episode, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            print(f"[podcast] warning: could not patch host personas: {exc}", file=sys.stderr)


        # Stamp series metadata early (HBR paths / --series / episode field) so
        # --no-upload runs still persist series on episode.json.
        _resolve_and_stamp_series(
            episode,
            explicit_series=getattr(args, "series", None),
            run_dir=out_dir,
            research_md=Path(args.research_md).resolve() if args.research_md else None,
            source_path=Path(args.source).resolve() if args.source else None,
        )

        # Fact-check against primary source when available (HBR / framework listens).

        # Same class as Enterprise factcheck_report.json — durable for Most AI etc.
        factcheck_warning = None
        if not getattr(args, "skip_factcheck", False):
            try:
                from factcheck_episode import fact_check_episode, find_source_text, factcheck_status_line
                source_text = find_source_text(out_dir, getattr(args, "source", None))
                if source_text:
                    print("[podcast] fact-checking episode against primary source…", file=sys.stderr)
                    episode, fc_report = fact_check_episode(
                        episode, source_text, out_dir=out_dir, use_gemini=True
                    )
                    summary = (fc_report or {}).get("summary") or {}
                    print(
                        f"[podcast] factcheck done: unsupported={summary.get('unsupported')} "
                        f"rewritten={summary.get('rewritten')} "
                        f"(wrote factcheck_report.json)",
                        file=sys.stderr,
                    )
                    # 2026-09-28: 0 checked claims is a warning, not a pass.
                    print(f"[podcast] factcheck {factcheck_status_line(fc_report)}", file=sys.stderr)
                    if summary.get("status") == "warning_unchecked":
                        factcheck_warning = factcheck_status_line(fc_report)
                else:
                    print(
                        "[podcast] no primary source file for fact-check "
                        "(hbr_source.md / *_source.txt / --source); skipping",
                        file=sys.stderr,
                    )
            except Exception as exc:
                print(f"[podcast] warning: fact-check step failed: {exc!r}", file=sys.stderr)

        # Fact-check rewrites / --episode-in scripts can carry markdown too.
        if _strip_episode_markdown(episode, "pre-TTS"):
            try:
                (out_dir / "episode.json").write_text(
                    json.dumps(episode, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            except OSError as exc:
                print(f"[podcast] warning: failed to re-save episode.json: {exc}", file=sys.stderr)

        segments = episode.get("segments") or []
        line_count = sum(len(s.get("lines") or []) for s in segments)
        print(f"[podcast] episode has {len(segments)} segments, {line_count} lines", file=sys.stderr)

        if (os.environ.get("TTS_BACKEND") or "kokoro").strip().lower() == "kokoro":
            _kokoro_cm = kokoro_lifecycle.kokoro_session(
                kokoro_lifecycle.default_base_url(),
                autostop=kokoro_lifecycle.autostop_enabled(
                    cli_disable=args.no_kokoro_autostop,
                ),
            )
        else:
            print(
                "[podcast] TTS backend is not Kokoro; not managing the kokoro container",
                file=sys.stderr,
            )
            _kokoro_cm = contextlib.nullcontext()
        # Ensure on enter. The session finally stops Kokoro on return, error,
        # or Ctrl+C. Returning before this point means this run did not use it.
        with _kokoro_cm:
            print("[podcast] synthesizing per-line audio via Kokoro…", file=sys.stderr)
            audio_resp = client.call("generate_audio", {
                "episode": episode,
                "out_dir": str(audio_dir),
            })
            if not isinstance(audio_resp, dict) or not audio_resp.get("clips"):
                print("[podcast] generate_audio returned no clips", file=sys.stderr)
                return 4
            clips = audio_resp["clips"]
            print(f"[podcast] {len(clips)} clips synthesized", file=sys.stderr)

            # Drop any zero-path clips (shouldn't happen here, but be defensive).
            clips = [c for c in clips if c.get("path")]

            final_wav = out_dir / "podcast.wav"
            print(f"[podcast] concatenating to {final_wav}…", file=sys.stderr)
            client.call("concatenate_audio_track", {
                "clips": clips,
                "out_path": str(final_wav),
            })

            # Drop the per-line WAV fragments once the final podcast WAV exists.
            if not args.keep_intermediates and audio_dir.exists():
                shutil.rmtree(audio_dir, ignore_errors=True)
                print(f"[podcast] cleaned up intermediates in {audio_dir}", file=sys.stderr)

            # --------- OPTIONAL VIDEO ASSEMBLY (Tier 2) ----------
            # Off by default. When --video is passed:
            #   1. Render PodcastBackground (looped base layer)
            #   2. compute_visual_timings → absolute start/end per visual
            #   3. Render each scripted visual via render_visual
            #   4. compose_final_video → podcast.mp4
            # Video is composed against the WAV so we don't inherit the 25 ms
            # MP3 priming delay; MP3 conversion is the very last step below.
            final_video_path = None
            if args.video:
                try:
                    final_video_path = _build_video(
                        client=client,
                        episode=episode,
                        clips=clips,
                        audio_for_video=final_wav,
                        out_dir=out_dir,
                        topic=args.topic,
                    )
                except Exception as exc:
                    print(f"[podcast] WARNING: video assembly failed: {exc}", file=sys.stderr)
                    # Audio still ships even if video failed.

            # Final step (both audio-only and video paths): compress WAV → MP3.
            # A typical 8-minute podcast at 64k mono is ~3.8 MB vs ~23 MB as WAV.
            final_mp3 = out_dir / "podcast.mp3"
            print(f"[podcast] compressing to {final_mp3} ({args.mp3_bitrate} mp3)…", file=sys.stderr)
            mp3_ok = False
            try:
                mp3_proc = subprocess.run(
                    [
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(final_wav),
                        "-c:a", "libmp3lame",
                        "-b:a", args.mp3_bitrate,
                        "-ac", "1",
                        str(final_mp3),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if mp3_proc.returncode != 0:
                    print(
                        f"[podcast] WARNING: mp3 transcode failed (exit {mp3_proc.returncode}); "
                        f"keeping WAV.\n  stderr: {mp3_proc.stderr.strip()[-500:]}",
                        file=sys.stderr,
                    )
                else:
                    mp3_ok = True
            except FileNotFoundError:
                print(
                    "[podcast] WARNING: ffmpeg not found on PATH; skipping mp3 compression.",
                    file=sys.stderr,
                )

            if mp3_ok:
                wav_size_mb = final_wav.stat().st_size / (1024 * 1024) if final_wav.exists() else 0
                mp3_size_mb = final_mp3.stat().st_size / (1024 * 1024)
                print(
                    f"[podcast] mp3 ready: {mp3_size_mb:.1f} MB "
                    f"(was {wav_size_mb:.1f} MB as wav, {mp3_size_mb / max(wav_size_mb, 0.01) * 100:.0f}% of original)",
                    file=sys.stderr,
                )
                # Delete the WAV by default to honor the "compress" intent.
                if not args.keep_wav and final_wav.exists():
                    try:
                        final_wav.unlink()
                        print("[podcast] removed uncompressed WAV (pass --keep-wav to keep it)", file=sys.stderr)
                    except OSError as exc:
                        print(f"[podcast] WARNING: could not delete WAV: {exc}", file=sys.stderr)

            # --- YouTube upload ---
            if final_video_path and final_video_path.exists() and not args.no_upload:
                source_for_series = Path(args.source).resolve() if getattr(args, "source", None) else None
                research_for_series = Path(args.research_md).resolve() if getattr(args, "research_md", None) else None
                series_key = _resolve_and_stamp_series(
                    episode,
                    explicit_series=getattr(args, "series", None),
                    run_dir=out_dir,
                    research_md=research_for_series,
                    source_path=source_for_series,
                )
                _try_youtube_upload(
                    out_dir, episode, final_video_path,
                    privacy_status=args.upload_privacy,
                    title_override=args.yt_title,
                    series=series_key,
                )

            final_audio_path = final_mp3 if mp3_ok else final_wav

            # --- Drive sync ---
            # Share exactly one artifact per run: the mp4 when video was produced,
            # otherwise the mp3. The Drive folder is the single source of truth for
            # finished episodes, so we don't want duplicate audio + video copies.
            drive_src = final_video_path if final_video_path and final_video_path.exists() else (
                final_mp3 if mp3_ok and final_mp3.exists() else None
            )
            if drive_src is not None:
                _copy_to_drive(drive_src, out_dir.name, disabled=args.no_upload or args.no_drive)

            if factcheck_warning:
                print(f"\n[podcast] {factcheck_warning}", file=sys.stderr)
            if final_video_path:
                print(f"\n[podcast] DONE: video={final_video_path}  audio={final_audio_path}")
            else:
                print(f"\n[podcast] DONE: {final_audio_path}")
            return 0
    finally:
        client.close()


def _copy_to_drive(src: Path, run_name: str, *, disabled: bool = False) -> None:
    """Copy a single finished artifact (mp4 or mp3) into the Drive sync folder
    under the run-dir name so it sorts alongside prior episodes. Best-effort:
    a missing Drive mount or copy failure is logged but not fatal."""
    if disabled or os.environ.get("GAKU_NO_DRIVE", "").strip().lower() in {"1", "true", "yes", "on"}:
        print("[podcast] Drive copy disabled; keeping output local", file=sys.stderr)
        return
    if not DRIVE_SYNC_DIR.is_dir():
        print(f"[podcast] WARNING: Drive sync folder not found: {DRIVE_SYNC_DIR} - skipping", file=sys.stderr)
        return
    dest = DRIVE_SYNC_DIR / f"{run_name}{src.suffix}"
    try:
        shutil.copyfile(src, dest)
        print(f"[podcast] copied to Drive: {dest}", file=sys.stderr)
    except OSError as exc:
        print(f"[podcast] WARNING: Drive copy failed ({src.name} -> {dest}): {exc}", file=sys.stderr)


def _build_video(
    client: "MCPClient",
    episode: dict[str, Any],
    clips: list[dict[str, Any]],
    audio_for_video: Path,
    out_dir: Path,
    topic: str,
) -> Path | None:
    """Segment Blocks: one full-screen visual per segment, plus open/close
    title cards. Timing comes from clip_durations.json (measured), not from
    Gemini guesses. SpeakerCard is baked into each SegmentCard, not a
    separate overlay layer."""
    print("[podcast] building video track (Segment Blocks)…", file=sys.stderr)

    visuals_dir = out_dir / "visuals"
    visuals_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load probed clip durations (written by concatenate_audio_track).
    clip_durations_path = out_dir / "clip_durations.json"
    if clip_durations_path.exists():
        clip_durations = json.loads(clip_durations_path.read_text(encoding="utf-8"))
    else:
        # Fallback: derive from the clips array.
        clip_durations = {
            "clips": [
                {"line_id": c.get("line_id"), "duration_ms": int(c.get("duration_ms", 0))}
                for c in clips
            ]
        }

    # 2. Build the visual manifest. This produces one visual per segment +
    #    open/close title cards, with absolute timings.
    from visual_queue import build_visual_manifest  # local import

    pexels_key = _load_env_pexels_key()
    manifest = build_visual_manifest(
        episode=episode,
        clip_durations=clip_durations,
        run_dir=out_dir,
        pexels_key=pexels_key,
        audio_path=audio_for_video,
        mcp_client=client,
    )
    visuals_in_manifest = list(manifest.get("visuals") or [])
    print(
        f"[podcast] manifest: {len(visuals_in_manifest)} visuals "
        f"(total {manifest.get('total_duration_ms', 0) / 1000:.1f}s)",
        file=sys.stderr,
    )

    # 3. Render every manifest entry.
    total = len(visuals_in_manifest)
    for i, v in enumerate(visuals_in_manifest, 1):
        visual_id = v.get("visual_id") or f"v{i:03d}"
        template = v.get("template")
        if not template:
            continue
        v_path = visuals_dir / f"visual_{i:03d}_{visual_id}.mp4"
        duration_ms = max(1000, int(v.get("end_ms", 0)) - int(v.get("start_ms", 0)))
        print(
            f"[podcast]   visual {i}/{total}: {template} "
            f"@ {v.get('start_ms', 0) / 1000:.1f}s for {duration_ms / 1000:.1f}s",
            file=sys.stderr,
        )
        try:
            client.call("render_visual", {
                "template": template,
                "data": v.get("data") or {},
                "duration_ms": duration_ms,
                "out_path": str(v_path),
            })
        except Exception as exc:
            print(f"[podcast]   FAILED to render visual {visual_id}: {exc}", file=sys.stderr)
            continue
        v["path"] = str(v_path)

    # Persist the manifest with rendered paths so compose_final_video can
    # pick them up via visual_manifest_path if the caller prefers.
    (out_dir / "visual_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 4. Compose: concat visuals + audio.
    rendered = [
        {
            "visual_id": v.get("visual_id"),
            "template": v.get("template"),
            "path": v["path"],
            "start_ms": int(v.get("start_ms", 0)),
            "end_ms": int(v.get("end_ms", 0)),
        }
        for v in visuals_in_manifest
        if v.get("path")
    ]
    final_mp4 = out_dir / "podcast.mp4"
    print(f"[podcast] composing final video → {final_mp4}…", file=sys.stderr)
    client.call("compose_final_video", {
        "audio_path": str(audio_for_video),
        "visuals": rendered,
        "out_path": str(final_mp4),
        "width": 1920,
        "height": 1080,
        "fps": 30,
    })

    size_mb = final_mp4.stat().st_size / (1024 * 1024) if final_mp4.exists() else 0
    print(f"[podcast] video ready: {size_mb:.1f} MB ({final_mp4})", file=sys.stderr)
    return final_mp4


def _load_env_pexels_key() -> str | None:
    """Read PEXELS_API_KEY from podcast-pipeline/.env or process env.

    Minimal parser — handles `KEY=value` lines, ignores blanks/comments,
    strips surrounding quotes. Doesn't pull in python-dotenv just for one
    key.
    """
    existing = os.environ.get("PEXELS_API_KEY")
    if existing:
        return existing.strip() or None
    env_path = ROOT / ".env"
    if not env_path.exists():
        return None
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() != "PEXELS_API_KEY":
                continue
            v = v.strip().strip('"').strip("'")
            return v or None
    except OSError:
        return None
    return None


def _voice_display_names(episode: dict[str, Any]) -> dict[str, str]:
    """Map host_id -> display name pulled from the resolved Kokoro voice_id.

    `bm_george` -> "George", `bf_emma` -> "Emma", `af_bella` -> "Bella", etc.
    Falls back to the episode's host.name field.
    """
    out: dict[str, str] = {}
    for h in episode.get("hosts") or []:
        hid = h.get("id")
        if not hid:
            continue
        voice = (h.get("voice_id") or "").strip()
        # voice ids look like `bm_george` / `af_bella` — strip the prefix.
        name_part = voice.split("_", 1)[1] if "_" in voice else voice
        display = name_part.replace("v0", "").strip()
        out[hid] = display.title() if display else (h.get("name") or f"Host {hid}")
    return out



def _resolve_and_stamp_series(
    episode: dict[str, Any],
    *,
    explicit_series: str | None,
    run_dir: Path,
    research_md: Path | None = None,
    source_path: Path | None = None,
) -> str | None:
    """Detect series_key, stamp episode["series"], persist episode.json when changed."""
    if str(REPO_ROOT / "podcast-pipeline") not in sys.path:
        # youtube_playlists.py lives beside this file
        sys.path.insert(0, str(ROOT))
    try:
        import youtube_playlists as ytpl
    except ImportError:
        print("[youtube] youtube_playlists not importable; skipping series stamp", file=sys.stderr)
        return (explicit_series or episode.get("series") or None)

    series_key = ytpl.detect_series(
        explicit=explicit_series,
        episode=episode,
        run_dir=run_dir,
        research_md=research_md,
        source_path=source_path,
        topic=episode.get("topic"),
        tags=episode.get("keywords") or episode.get("tags"),
    )
    if not series_key:
        return None
    if episode.get("series") != series_key:
        episode["series"] = series_key
        ep_path = run_dir / "episode.json"
        try:
            if True:  # always persist series stamp when resolved
                ep_path.write_text(
                    json.dumps(episode, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                print(f"[podcast] series={series_key} stamped on {ep_path.name}", file=sys.stderr)
        except OSError as exc:
            print(f"[podcast] warning: could not stamp series on episode.json: {exc}", file=sys.stderr)
    return series_key


def _try_youtube_upload(
    run_dir: Path,
    episode: dict[str, Any],
    mp4_path: Path,
    *,
    privacy_status: str = "private",
    title_override: str | None = None,
    series: str | None = None,
) -> None:
    """Upload the rendered podcast.mp4 to YouTube. Skips gracefully if
    credentials aren't present; logs and swallows errors so a failed upload
    never breaks the pipeline (the MP4 is still on disk).

    series: optional series_key (e.g. hbr). When set (or present on episode),
    youtube_upload formats the title and adds the video to the series playlist.
    """
    client_secrets = REPO_ROOT / "youtube_client_secrets.json"
    if not client_secrets.exists():
        print("[youtube] skipping upload — youtube_client_secrets.json not found")
        print("[youtube] to enable: place OAuth client secrets file in project root")
        return
    # youtube_upload.py lives in the repo root; its module-level relative paths
    # for the secrets/token files assume the CWD is the repo root, so cd there
    # for the duration of the upload.
    import os
    prev_cwd = os.getcwd()
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        os.chdir(REPO_ROOT)
        import youtube_upload as yt
        import datetime
        topic = episode.get("topic", "GAKU Podcast")
        segments = len(episode.get("segments", []))
        description = (
            f"{topic}\n\n"
            f"Generated by PROJECT GAKU — {segments} segments — "
            f"{datetime.date.today().strftime('%B %d, %Y')}\n\n"
            f"This video was auto-generated using AI research, scripting, and text-to-speech synthesis.\n\n"
            f"This episode uses AI-generated voices and visuals (PROJECT GAKU)."
        )
        tags = episode.get("keywords", []) or ["podcast", "education", "ai", "langchain"]
        title = (title_override or topic)[:100]
        series_key = (series or episode.get("series") or None)
        if isinstance(series_key, str):
            series_key = series_key.strip().lower() or None
        result = yt.upload_video(
            str(mp4_path),
            title=title,
            description=description,
            tags=tags,
            privacy_status=privacy_status,
            series=series_key,
            episode_dir=str(run_dir) if run_dir else None,
            episode=episode,
        )
        out = {**result, "uploaded_at": datetime.datetime.utcnow().isoformat()}
        if series_key:
            out.setdefault("series", series_key)
        (run_dir / "youtube.json").write_text(json.dumps(out, indent=2))
    except Exception as e:
        print(f"[youtube] upload failed: {e}")
        print(f"[youtube] video is still saved locally at: {mp4_path}")
    finally:
        os.chdir(prev_cwd)


if __name__ == "__main__":
    # os._exit() bypasses interpreter cleanup so any lingering non-daemon
    # threads or live refs to OS handles (Kokoro keep-alive sockets, ffmpeg
    # progress pipes, the Remotion CLI's child handles routed through the
    # MCP server) can't keep the process alive after we're logically done.
    # main()'s finally already shut down the MCP child tree via
    # MCPClient.close(); this is the belt-and-braces guard.
    _rc = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_rc)
