"""Map a VibeVoice one-pass segment WAV back to per-line time boundaries.

VibeVoice generates a whole tagged dialogue in a single pass, so there are no
per-line boundaries — but GAKU's visual timing depends on them. This script runs
faster-whisper word alignment (via whisper_align.py) over the segment audio,
then aligns the emitted word stream to the KNOWN ordered line texts using a
difflib sequence match, and emits contiguous [start_ms, end_ms] per line.

Contiguous by construction: line[i].end_ms == line[i+1].start_ms, line[0] starts
at 0, and the last line ends at the probed audio duration — so slicing the WAV at
these boundaries tiles the whole file with no gaps or dropped audio.

Usage:
    python align_lines.py --audio seg.flac --lines lines.json --out boundaries.json

    lines.json : [{"line_id": "...", "text": "..."}, ...]  in playback order
    boundaries.json (output):
        {"lines": [{"line_id","start_ms","end_ms","duration_ms","matched_words"}],
         "audio_duration_ms": int, "word_count": int}
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

import whisper_align


def _norm(tok: str) -> str:
    return re.sub(r"[^a-z0-9]", "", tok.lower())


def _tokenize(text: str) -> list[str]:
    return [t for t in (_norm(w) for w in text.split()) if t]


def _probe_duration_ms(path: Path) -> int:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return int(round(float(r.stdout.strip()) * 1000))
    except ValueError:
        return 0


def align_lines(audio_path: Path, lines: list[dict]) -> dict:
    audio_ms = _probe_duration_ms(audio_path)

    # Whisper word stream with times (relative to audio start).
    words = whisper_align.align(audio_path, language="en", use_cache=False)
    asr_norm = [_norm(w["word"]) for w in words]
    asr_valid = [i for i, t in enumerate(asr_norm) if t]  # drop empty/punct-only

    # Flat expected token list, each tagged with its line index.
    exp_tokens: list[str] = []
    exp_line_of: list[int] = []
    for li, ln in enumerate(lines):
        for t in _tokenize(ln["text"]):
            exp_tokens.append(t)
            exp_line_of.append(li)

    # Sequence-align expected tokens ↔ ASR tokens (only the non-empty ASR ones).
    asr_seq = [asr_norm[i] for i in asr_valid]
    sm = difflib.SequenceMatcher(a=exp_tokens, b=asr_seq, autojunk=False)

    # For each expected token index, the matched ASR word index (or None).
    exp_to_asr: list[int | None] = [None] * len(exp_tokens)
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            exp_to_asr[a + k] = asr_valid[b + k]

    # First expected token index of each line.
    first_exp_of_line: list[int | None] = [None] * len(lines)
    for ei, li in enumerate(exp_line_of):
        if first_exp_of_line[li] is None:
            first_exp_of_line[li] = ei

    def line_start_ms(li: int) -> int | None:
        """Start = time of the first ASR word matched to a token IN this line."""
        for ei, lidx in enumerate(exp_line_of):
            if lidx != li:
                continue
            ai = exp_to_asr[ei]
            if ai is not None:
                return words[ai]["start_ms"]
        return None

    # Raw starts (may be None if a line had no matched words).
    raw_starts: list[int | None] = [line_start_ms(li) for li in range(len(lines))]

    # Force monotonic, contiguous boundaries. line[0] = 0; fill gaps by carrying
    # the previous known start; last end = audio duration.
    starts: list[int] = []
    prev = 0
    for li in range(len(lines)):
        s = raw_starts[li]
        if li == 0 or s is None or s < prev:
            s = prev if li > 0 else 0
        starts.append(s)
        prev = s
    starts[0] = 0

    out_lines = []
    for li, ln in enumerate(lines):
        start = starts[li]
        end = starts[li + 1] if li + 1 < len(lines) else audio_ms
        if end < start:
            end = start
        # count matched words in this line for a confidence signal
        matched = sum(
            1 for ei, lidx in enumerate(exp_line_of)
            if lidx == li and exp_to_asr[ei] is not None
        )
        out_lines.append({
            "line_id": ln["line_id"],
            "start_ms": start,
            "end_ms": end,
            "duration_ms": end - start,
            "matched_words": matched,
        })

    return {
        "lines": out_lines,
        "audio_duration_ms": audio_ms,
        "word_count": len(words),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--lines", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    lines = json.loads(Path(args.lines).read_text(encoding="utf-8"))
    result = align_lines(Path(args.audio), lines)
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        f"[align_lines] {len(result['lines'])} lines, "
        f"{result['word_count']} words, audio {result['audio_duration_ms']}ms",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
