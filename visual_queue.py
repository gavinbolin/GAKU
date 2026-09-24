"""
Segment Blocks visual queue.
Produces one visual per podcast segment plus open/close title cards.
Timing comes from measured audio clip durations, not guesses.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

COLOR_PRIMARY = "#7C3AED"  # legacy default — kept for backward compat
COLOR_SECONDARY = "#0EA5E9"  # legacy default — kept for backward compat

# ---------- Voice color registry (JSON source of truth) ----------
# Humans edit: PROJECT GAKU/voice_colors.json  (see docs/voice_colors.md)
# Remotion never keeps its own table — colors arrive as SegmentCard props.
_VOICE_COLORS_PATH = Path(__file__).resolve().parent.parent / "voice_colors.json"
_DEFAULT_PALETTE: list[str] = [
    "#7C3AED", "#0EA5E9", "#F97316", "#10B981",
    "#EC4899", "#F59E0B", "#6366F1", "#F43F5E",
    "#14B8A6", "#A855F7", "#22D3EE", "#E879F9",
    "#84CC16", "#FB7185", "#38BDF8",
]


def _load_voice_colors() -> dict[str, Any]:
    """Load voice_colors.json; return a mutable dict with voices + palette_next."""
    path = _VOICE_COLORS_PATH
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("voices"), dict):
                data.setdefault("palette_next", list(_DEFAULT_PALETTE))
                return data
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[visual_queue] warn: could not read {path}: {exc}", file=sys.stderr)
    return {
        "version": 1,
        "voices": {},
        "palette_next": list(_DEFAULT_PALETTE),
    }


def _save_voice_colors(data: dict[str, Any]) -> None:
    try:
        _VOICE_COLORS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _VOICE_COLORS_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[visual_queue] warn: could not write {_VOICE_COLORS_PATH}: {exc}", file=sys.stderr)


def _derive_display_name(voice_id: str) -> str:
    vid = (voice_id or "").strip()
    # VibeVoice refs: Serana_ref.wav -> Serana
    if vid.lower().endswith(".wav"):
        stem = Path(vid).stem
        stem = re.sub(r"[_-]?ref$", "", stem, flags=re.IGNORECASE)
        stem = stem.replace("_", " ").strip()
        return stem.title() if stem else "Speaker"
    # Kokoro: bm_george / bf_v0isabella -> George / Isabella
    name_part = vid.split("_", 1)[1] if "_" in vid else vid
    name_part = re.sub(r"^v0", "", name_part, flags=re.IGNORECASE).strip()
    return name_part.replace("_", " ").title() if name_part else "Speaker"


def _pick_unused_color(data: dict[str, Any]) -> str:
    used = {
        str(v.get("color") or "").upper()
        for v in (data.get("voices") or {}).values()
        if isinstance(v, dict)
    }
    palette = list(data.get("palette_next") or []) + list(_DEFAULT_PALETTE)
    for c in palette:
        if c.upper() not in used:
            return c
    # Exhausted palette — hash a fresh-ish distinct hex from size.
    n = len(used) + 1
    return f"#{(n * 47) % 256:02X}{(n * 91) % 256:02X}{(n * 13) % 256:02X}"


def voice_persona(voice_id: str, *, persist_unknown: bool = True) -> dict[str, str]:
    """Resolve a voice_id (Kokoro id or VibeVoice ref filename) to {name, color}.

    Looks up PROJECT GAKU/voice_colors.json. Unknown ids get a derived name +
    next unused palette color, then are appended to the JSON so the mapping
    stays stable on the next run.
    """
    vid = (voice_id or "").strip()
    data = _load_voice_colors()
    voices = data.get("voices") or {}
    entry = voices.get(vid) if vid else None
    if isinstance(entry, dict) and entry.get("color"):
        name = (entry.get("display_name") or entry.get("name") or _derive_display_name(vid)).strip()
        return {"name": name or _derive_display_name(vid), "color": str(entry["color"])}

    name = _derive_display_name(vid) if vid else "Speaker"
    color = _pick_unused_color(data)
    if vid and persist_unknown:
        voices[vid] = {
            "display_name": name,
            "color": color,
            "notes": "auto-added by visual_queue.voice_persona",
        }
        data["voices"] = voices
        # Drop the color we just consumed from palette_next if present.
        next_p = [c for c in (data.get("palette_next") or []) if c.upper() != color.upper()]
        data["palette_next"] = next_p
        _save_voice_colors(data)
        print(f"[visual_queue] registered new voice {vid!r} -> {name} {color}", file=sys.stderr)
    return {"name": name, "color": color}


# Lazy snapshot for any legacy rom visual_queue import VOICE_REGISTRY imports.
def _voice_registry_snapshot() -> dict[str, dict[str, str]]:
    data = _load_voice_colors()
    out: dict[str, dict[str, str]] = {}
    for k, v in (data.get("voices") or {}).items():
        if isinstance(v, dict) and v.get("color"):
            out[k] = {
                "name": str(v.get("display_name") or v.get("name") or _derive_display_name(k)),
                "color": str(v["color"]),
            }
    return out


VOICE_REGISTRY = _voice_registry_snapshot()
_FALLBACK_COLORS = list(_DEFAULT_PALETTE)


def _host_personas(episode: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Return ({name, color}, {name, color}) for hosts A and B from voice_colors.json."""
    a = {"name": "Host A", "color": _DEFAULT_PALETTE[0]}
    b = {"name": "Host B", "color": _DEFAULT_PALETTE[1]}
    for h in episode.get("hosts") or []:
        if h.get("id") == "A":
            a = voice_persona(h.get("voice_id") or "")
        elif h.get("id") == "B":
            b = voice_persona(h.get("voice_id") or "")
    if a["color"] == b["color"]:
        # Force a distinct B color without mutating the registry entry.
        for c in _DEFAULT_PALETTE:
            if c.upper() != a["color"].upper():
                b = {**b, "color": c}
                break
    return a, b


# Open/close title card duration (ms).
TITLE_CARD_MS = 5000

# Discourse markers that, when they appear as the very first token of a
# line, can safely be stripped without changing the meaning. We only strip
# from the front — "like" mid-sentence is usually a preposition we'd
# corrupt by removing.
_LEADING_FILLER_RE = re.compile(
    r"^(?:"
    r"yeah[,!.]?|yep[,!.]?|right[,!.]?|okay[,!.]?|ok[,!.]?|"
    r"oh[,!.]?|well[,!.]?|so[,!.]?|um[,!.]?|uh[,!.]?|"
    r"wow[,!.]?|exactly[,!.]?|precisely[,!.]?|indeed[,!.]?|truly[,!.]?|"
    r"actually[,!.]?|basically[,!.]?|literally[,!.]?|"
    r"you know[,!.]?|i mean[,!.]?|"
    r"that'?s a great (?:point|question|catch)[,!.]?"
    r")\s+",
    re.IGNORECASE,
)

# In-sentence filler chunks ("...you know," "...I mean,") — only strip
# when followed by a comma to avoid eating real content.
_INLINE_FILLER_RE = re.compile(
    r",\s*(?:you know|i mean|um|uh)\s*,",
    re.IGNORECASE,
)


def _load_clip_durations(clip_durations: Any) -> dict[str, int]:
    """Accept either {"clips": [...]} or a plain dict mapping line_id -> ms."""
    if isinstance(clip_durations, dict) and "clips" in clip_durations:
        out: dict[str, int] = {}
        for c in clip_durations.get("clips") or []:
            lid = c.get("line_id")
            dur = c.get("duration_ms")
            if lid and dur is not None:
                out[str(lid)] = int(dur)
        return out
    if isinstance(clip_durations, dict):
        return {str(k): int(v) for k, v in clip_durations.items()}
    return {}


_PART_TITLE_RE = re.compile(r"\s*\(part\s*\d+(?:\s*/\s*\d+)?\)\s*", re.IGNORECASE)


def _clean_segment_title(title: str) -> str:
    cleaned = title or ""
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = _PART_TITLE_RE.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" -–—") or (title or "Segment")


def compute_segment_timings(
    episode: dict[str, Any],
    clip_durations: Any,
) -> list[dict[str, Any]]:
    """Walk the episode in order and produce per-segment absolute timings.

    For each line we add the probed audio duration + the pause_after_ms from
    the episode script. The result is one entry per segment with start_ms,
    end_ms, duration_ms.
    """
    durations = _load_clip_durations(clip_durations)
    cursor = 0
    out: list[dict[str, Any]] = []
    for idx, seg in enumerate(episode.get("segments") or []):
        seg_start = cursor
        for line in seg.get("lines") or []:
            lid = line.get("line_id")
            cursor += int(durations.get(lid, 0))
            cursor += int(line.get("pause_after_ms") or 0)
        out.append({
            "segment_idx": idx,
            "segment_id": seg.get("segment_id"),
            "start_ms": seg_start,
            "end_ms": cursor,
            "duration_ms": cursor - seg_start,
        })
    return out


_WORD_RE = re.compile(r"\S+")

# Time deducted from the end of each line before distributing across words,
# so the highlight settles before the next line starts.
SENTENCE_END_PAUSE_MS = 200


def _word_weight(word: str) -> float:
    """Length-bias weight for distributing a line's duration across its words.

    Short tokens (< 4 chars, e.g. "is", "a", "to") get 80% of average;
    long tokens (> 7 chars) get 120%; everything else is 1.0. The stripped
    length ignores trailing punctuation so "phone." weighs the same as "phone".
    """
    bare = re.sub(r"[^\w']", "", word)
    n = len(bare)
    if n == 0:
        return 1.0
    if n < 4:
        return 0.8
    if n > 7:
        return 1.2
    return 1.0


def _line_bounds(
    episode: dict[str, Any],
    durations: dict[str, int],
) -> list[dict[str, Any]]:
    """Per-line absolute time ranges on the full podcast timeline.

    Each entry covers exactly the audio range [start_ms, end_ms) of one line
    (excluding the `pause_after_ms` silence that follows). We also carry the
    seg/line indices and host_id so words can be re-bucketed back to lines.
    """
    cursor = 0
    bounds: list[dict[str, Any]] = []
    for seg_idx, seg in enumerate(episode.get("segments") or []):
        for line_idx, line in enumerate(seg.get("lines") or []):
            lid = line.get("line_id")
            clip_ms = int(durations.get(lid, 0))
            bounds.append({
                "seg_idx": seg_idx,
                "line_idx": line_idx,
                "host_id": line.get("host_id") or "A",
                "text": (line.get("text") or "").strip(),
                "start_ms": cursor,
                "end_ms": cursor + clip_ms,
                "clip_ms": clip_ms,
            })
            cursor += clip_ms
            cursor += int(line.get("pause_after_ms") or 0)
    return bounds


def _proportional_word_timings(
    episode: dict[str, Any],
    durations: dict[str, int],
) -> list[dict[str, Any]]:
    """Original length-biased proportional distribution. Used as a fallback
    when WhisperX alignment is unavailable or produces no words.
    """
    cursor = 0
    out: list[dict[str, Any]] = []
    for seg_idx, seg in enumerate(episode.get("segments") or []):
        for line_idx, line in enumerate(seg.get("lines") or []):
            lid = line.get("line_id")
            clip_ms = int(durations.get(lid, 0))
            speaker = line.get("host_id") or "A"
            text = (line.get("text") or "").strip()
            words = _WORD_RE.findall(text)

            if not words or clip_ms <= 0:
                cursor += clip_ms
                cursor += int(line.get("pause_after_ms") or 0)
                continue

            pause = min(SENTENCE_END_PAUSE_MS, max(0, clip_ms // 3))
            active = max(1, clip_ms - pause)

            weights = [_word_weight(w) for w in words]
            wsum = sum(weights) or float(len(words))
            raw = [w / wsum * active for w in weights]
            floors = [int(r) for r in raw]
            remainder = active - sum(floors)
            frac_order = sorted(
                range(len(words)),
                key=lambda i: (raw[i] - floors[i]),
                reverse=True,
            )
            for i in frac_order[:max(0, remainder)]:
                floors[i] += 1

            t = cursor
            for i, w in enumerate(words):
                start_ms = t
                end_ms = t + floors[i]
                out.append({
                    "word": w,
                    "speaker": speaker,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "segment_idx": seg_idx,
                    "line_idx": line_idx,
                })
                t = end_ms

            cursor += clip_ms
            cursor += int(line.get("pause_after_ms") or 0)

    return out


def _norm_tok(tok: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (tok or "").lower())


def _script_token_stream(
    episode: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Flatten episode lines into expected tokens + per-token metadata.

    Each meta entry: {word (original script token), speaker, segment_idx, line_idx}.
    """
    tokens: list[str] = []
    meta: list[dict[str, Any]] = []
    for seg_idx, seg in enumerate(episode.get("segments") or []):
        for line_idx, line in enumerate(seg.get("lines") or []):
            speaker = line.get("host_id") or "A"
            text = (line.get("text") or "").strip()
            for raw in _WORD_RE.findall(text):
                norm = _norm_tok(raw)
                if not norm:
                    continue
                tokens.append(norm)
                meta.append({
                    "word": raw,
                    "speaker": speaker,
                    "segment_idx": seg_idx,
                    "line_idx": line_idx,
                })
    return tokens, meta


def _stretch_word_ends(words: list[dict[str, Any]], *, max_pad_ms: int = 320) -> None:
    """Extend each word's end toward the next word's start so karaoke highlight
    doesn't flicker off mid-phoneme. Caps the pad so long pauses still clear.
    """
    for i, w in enumerate(words):
        start = int(w["start_ms"])
        end = int(w["end_ms"])
        if end < start + 40:
            end = start + 40
        if i + 1 < len(words):
            nxt = int(words[i + 1]["start_ms"])
            if nxt > end:
                end = min(nxt, end + max_pad_ms)
                # If the gap is small, fill it entirely so highlight is continuous.
                if nxt - end <= 80:
                    end = nxt
        w["end_ms"] = max(end, start + 1)


def _bucket_aligned_words(
    aligned: list[dict[str, Any]],
    bounds: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map whisper-aligned words (timestamps relative to the full podcast WAV)
    into the per-line buckets defined by `bounds`. Each aligned word lands in
    the bucket whose audio range covers its midpoint; words that fall in a
    `pause_after_ms` gap snap to the nearest neighbouring bucket.

    Prefer `_script_anchored_timings` — this path keeps Whisper's own tokens
    (which can diverge from the script) and is only a fallback.
    """
    if not aligned or not bounds:
        return []

    out: list[dict[str, Any]] = []
    bi = 0  # pointer into bounds — both lists are time-sorted
    n_bounds = len(bounds)
    for w in aligned:
        start = int(w.get("start_ms", 0))
        end = int(w.get("end_ms", start))
        mid = (start + end) // 2

        # Advance bi while we're past the current bucket's end.
        while bi < n_bounds - 1 and mid > bounds[bi]["end_ms"]:
            # If mid still hasn't entered the next bucket's start, decide by
            # proximity. Otherwise keep advancing.
            nxt = bounds[bi + 1]
            if mid < nxt["start_ms"]:
                # We're in the inter-line pause. Snap to whichever is closer.
                d_prev = mid - bounds[bi]["end_ms"]
                d_next = nxt["start_ms"] - mid
                if d_next < d_prev:
                    bi += 1
                break
            bi += 1

        b = bounds[bi]
        token = (w.get("word") or "").strip()
        if not token:
            continue
        out.append({
            "word": token,
            "speaker": b["host_id"],
            "start_ms": start,
            "end_ms": max(end, start + 1),
            "segment_idx": b["seg_idx"],
            "line_idx": b["line_idx"],
        })
    _stretch_word_ends(out)
    return out


def _script_anchored_timings(
    episode: dict[str, Any],
    aligned: list[dict[str, Any]],
    bounds: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Force-align SCRIPT tokens to Whisper word times via difflib.

    Karaoke captions should show the written dialogue, timed by ASR. Matching
    by sequence (not by time-bucket alone) recovers from Whisper substitutions
    and keeps host/line metadata from the episode script.
    """
    import difflib

    exp_tokens, exp_meta = _script_token_stream(episode)
    if not exp_tokens or not aligned:
        return []

    asr_norm = [_norm_tok(w.get("word") or "") for w in aligned]
    asr_valid = [i for i, t in enumerate(asr_norm) if t]
    asr_seq = [asr_norm[i] for i in asr_valid]
    if not asr_seq:
        return []

    sm = difflib.SequenceMatcher(a=exp_tokens, b=asr_seq, autojunk=False)
    exp_to_asr: list[int | None] = [None] * len(exp_tokens)
    matched = 0
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            exp_to_asr[a + k] = asr_valid[b + k]
            matched += 1

    # Reject pathological matches (e.g. Whisper hallucinated a different script).
    ratio = matched / max(1, len(exp_tokens))
    if ratio < 0.45:
        print(
            f"[visual_queue] script-anchor match too low ({ratio:.0%}); "
            "falling back to time-bucket",
            file=sys.stderr,
        )
        return []

    # Assign times: matched tokens take Whisper start/end; gaps interpolate
    # between neighbouring matched anchors (or line bounds as outer rails).
    starts: list[int | None] = [None] * len(exp_tokens)
    ends: list[int | None] = [None] * len(exp_tokens)
    for i, ai in enumerate(exp_to_asr):
        if ai is None:
            continue
        starts[i] = int(aligned[ai].get("start_ms", 0))
        ends[i] = int(aligned[ai].get("end_ms", starts[i]))

    # Outer rails from line bounds when available.
    bound_by_line: dict[tuple[int, int], dict[str, int]] = {}
    for b in bounds:
        bound_by_line[(b["seg_idx"], b["line_idx"])] = b

    def _interp_gaps() -> None:
        n = len(exp_tokens)
        i = 0
        while i < n:
            if starts[i] is not None:
                i += 1
                continue
            j = i
            while j < n and starts[j] is None:
                j += 1
            # Gap [i, j)
            left_end = ends[i - 1] if i > 0 and ends[i - 1] is not None else None
            right_start = starts[j] if j < n and starts[j] is not None else None
            if left_end is None or right_start is None:
                # Fall back to line bound span split evenly.
                meta = exp_meta[i]
                b = bound_by_line.get((meta["segment_idx"], meta["line_idx"]))
                rail0 = int(b["start_ms"]) if b else (left_end or 0)
                rail1 = int(b["end_ms"]) if b else (right_start or rail0 + 200)
                if left_end is not None:
                    rail0 = left_end
                if right_start is not None:
                    rail1 = right_start
            else:
                rail0, rail1 = left_end, right_start
            span = max(1, rail1 - rail0)
            gap_n = j - i
            for k in range(gap_n):
                s = rail0 + (span * k) // gap_n
                e = rail0 + (span * (k + 1)) // gap_n
                starts[i + k] = s
                ends[i + k] = max(e, s + 1)
            i = j

    _interp_gaps()

    out: list[dict[str, Any]] = []
    prev_end = 0
    for i, meta in enumerate(exp_meta):
        s = int(starts[i] if starts[i] is not None else prev_end)
        e = int(ends[i] if ends[i] is not None else s + 80)
        if s < prev_end:
            s = prev_end
        if e <= s:
            e = s + 40
        out.append({
            "word": meta["word"],
            "speaker": meta["speaker"],
            "start_ms": s,
            "end_ms": e,
            "segment_idx": meta["segment_idx"],
            "line_idx": meta["line_idx"],
        })
        prev_end = e

    _stretch_word_ends(out)
    print(
        f"[visual_queue] script-anchored {len(out)} words "
        f"(match {ratio:.0%} of script tokens)",
        file=sys.stderr,
    )
    return out


def compute_word_timings(
    episode: dict[str, Any],
    clip_durations: Any,
    audio_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Per-word absolute timings on the podcast timeline.

    When `audio_path` points to the concatenated podcast WAV (or any audio
    covering the same timeline), we run whisper-based forced alignment and
    map timings onto SCRIPT tokens (difflib sequence match). Otherwise — or
    if alignment fails / match is too weak — we fall back to time-bucketing
    Whisper tokens, then to the length-biased proportional estimator.

    Returned dicts have: word, speaker ("A"/"B"), start_ms, end_ms,
    segment_idx, line_idx.
    """
    durations = _load_clip_durations(clip_durations)

    aligned: list[dict[str, Any]] = []
    if audio_path is not None:
        ap = Path(audio_path)
        if ap.exists():
            try:
                from whisper_align import align as _wx_align  # local import — keeps torch off the import path when video is disabled
                # Seed Whisper with the script so it prefers the written words
                # over free transcription (cleaner karaoke tokens + times).
                prompt_bits: list[str] = []
                for seg in (episode.get("segments") or [])[:3]:
                    for line in (seg.get("lines") or [])[:6]:
                        t = (line.get("text") or "").strip()
                        if t:
                            prompt_bits.append(t)
                initial_prompt = " ".join(prompt_bits)[:800] or None
                print(f"[visual_queue] aligning {ap.name} via whisper…", file=sys.stderr)
                aligned = _wx_align(ap, initial_prompt=initial_prompt)
                print(f"[visual_queue] whisper produced {len(aligned)} words", file=sys.stderr)
            except Exception as exc:
                print(f"[visual_queue] whisper alignment failed ({exc!r}); using proportional", file=sys.stderr)
                aligned = []
        else:
            print(f"[visual_queue] audio_path missing ({ap}); using proportional", file=sys.stderr)

    if aligned:
        bounds = _line_bounds(episode, durations)
        anchored = _script_anchored_timings(episode, aligned, bounds)
        if anchored:
            return anchored
        bucketed = _bucket_aligned_words(aligned, bounds)
        if bucketed:
            return bucketed
        print("[visual_queue] alignment bucketing returned 0 words; falling back", file=sys.stderr)

    return _proportional_word_timings(episode, durations)


# Gap (ms) between consecutive words that forces a sentence break even if
# there's no terminal punctuation. Tuned to ~natural in-breath length.
SENTENCE_GAP_MS = 400


def group_into_sentences(word_timings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group a flat WhisperX-style word list into sentences.

    A sentence boundary fires on the FIRST of:
      - the word ends in `.`, `!`, or `?` (full stop / question / exclaim)
      - the cue already has >= 10 words AND the token ends with `,` (soft break
        only when the line would otherwise get too long for karaoke)
      - the gap to the next word exceeds SENTENCE_GAP_MS

    Returns a list of `{words, start_ms, end_ms, speaker}` dicts. Each word
    inside `words` is `{word, start_ms, end_ms, speaker}` — same metadata as
    the input, no segment/line indices.
    """
    if not word_timings:
        return []

    sentences: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    n = len(word_timings)
    for i, w in enumerate(word_timings):
        current.append(w)
        token = (w.get("word") or "").strip()
        ends_with_stop = bool(token) and token[-1] in ".!?"
        soft_comma = (
            bool(token)
            and token[-1] == ","
            and len(current) >= 10
        )
        large_gap = False
        if i + 1 < n:
            gap = int(word_timings[i + 1].get("start_ms", 0)) - int(w.get("end_ms", 0))
            if gap > SENTENCE_GAP_MS:
                large_gap = True
        is_last = i == n - 1
        if ends_with_stop or soft_comma or large_gap or is_last:
            counts: dict[str, int] = {}
            for cw in current:
                sp = cw.get("speaker") or "A"
                counts[sp] = counts.get(sp, 0) + 1
            speaker = max(counts.items(), key=lambda kv: kv[1])[0] if counts else "A"
            sentences.append({
                "words": [
                    {
                        "word": cw.get("word", ""),
                        "speaker": cw.get("speaker") or "A",
                        "start_ms": int(cw.get("start_ms", 0)),
                        "end_ms": int(cw.get("end_ms", 0)),
                    }
                    for cw in current
                ],
                "start_ms": int(current[0].get("start_ms", 0)),
                "end_ms": int(current[-1].get("end_ms", 0)),
                "speaker": speaker,
            })
            current = []
    return sentences


def _strip_caption_markdown(text: str) -> str:
    cleaned = text or ""
    cleaned = cleaned.replace("`", "")
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    cleaned = re.sub(r"_([^_]+)_", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _strip_fillers(text: str) -> str:
    cleaned = _strip_caption_markdown(text)
    # Strip leading discourse markers — possibly several in a row.
    for _ in range(3):
        new = _LEADING_FILLER_RE.sub("", cleaned, count=1)
        if new == cleaned:
            break
        cleaned = new
    cleaned = _INLINE_FILLER_RE.sub(",", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^[,.\s]+", "", cleaned)
    # Capitalize first character so the bullet reads as a complete thought.
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def _trim_to(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1].rstrip()
    # Try to break at a word boundary.
    sp = cut.rfind(" ")
    if sp > max_len * 0.6:
        cut = cut[:sp].rstrip()
    return cut.rstrip(",.;:") + "…"


def _extract_bullets(segment: dict[str, Any], max_bullets: int = 5) -> list[str]:
    """Pick 4-6 informative bullet points from the segment's dialogue.

    Strategy: take each line's text, strip fillers, drop short ones (<6 words
    after cleaning, e.g. "Like what?"), score by length+informativeness, and
    take the top N preserving conversational order.
    """
    candidates: list[tuple[int, int, str]] = []  # (score, original_idx, text)
    for i, line in enumerate(segment.get("lines") or []):
        raw = (line.get("text") or "").strip()
        if not raw:
            continue
        clean = _strip_fillers(raw)
        words = clean.split()
        if len(words) < 6:
            continue
        # Drop leading "And", "But" connectors — they read awkwardly as a
        # bullet's opening word.
        clean = re.sub(r"^(and |but )", "", clean, flags=re.IGNORECASE)
        if clean and clean[0].islower():
            clean = clean[0].upper() + clean[1:]
        trimmed = _trim_to(clean, 80)
        # Score: length is the primary signal; quotable lines get a bump.
        score = len(words)
        if line.get("is_quotable"):
            score += 6
        candidates.append((score, i, trimmed))

    # Take top-scoring, then re-sort by original order so bullets read
    # left-to-right as the conversation flowed.
    candidates.sort(key=lambda t: t[0], reverse=True)
    picked = candidates[:max_bullets]
    picked.sort(key=lambda t: t[1])
    bullets = [t[2] for t in picked]

    # Fallback: if we ended up with too few, fill from raw lines so the slide
    # isn't blank.
    if len(bullets) < 3:
        for line in segment.get("lines") or []:
            raw = _strip_fillers((line.get("text") or "").strip())
            if not raw:
                continue
            trimmed = _trim_to(raw, 80)
            if trimmed and trimmed not in bullets:
                bullets.append(trimmed)
            if len(bullets) >= 4:
                break
    return bullets[:max_bullets]


def _current_speaker(segment: dict[str, Any]) -> str:
    """Return 'A', 'B', or 'both' based on dialogue share in this segment."""
    counts: dict[str, int] = {"A": 0, "B": 0}
    for line in segment.get("lines") or []:
        hid = line.get("host_id")
        if hid in counts:
            counts[hid] += 1
    a, b = counts["A"], counts["B"]
    if a + b == 0:
        return "both"
    if a == 0:
        return "B"
    if b == 0:
        return "A"
    # Roughly equal → both. Otherwise the dominant speaker.
    if max(a, b) >= 2 * min(a, b):
        return "A" if a > b else "B"
    return "both"



_FIVE_RS = ("Roles", "Responsibilities", "Rituals", "Resources", "Results")
_STAT_NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})*(?:\.\d+)?%?|\d+%)")


def _segment_dialogue_blob(segment: dict[str, Any]) -> str:
    parts = []
    for ln in segment.get("lines") or []:
        parts.append(ln.get("text") or "")
    return " ".join(parts)


def _stat_value_grounded(value: str, segment_text: str) -> bool:
    """True if every numeric token in the overlay value appears in segment dialogue."""
    raw = (value or "").strip()
    if not raw:
        return False
    nums = _STAT_NUM_RE.findall(raw)
    if not nums:
        # Non-numeric stat string (e.g. qualitative) — allow
        return True
    blob = (segment_text or "").lower().replace(",", "")
    for tok in nums:
        n = tok.lower().replace(",", "")
        if n in blob or n.rstrip("%") in blob:
            continue
        if n.endswith("%") and (n[:-1] + " percent") in blob:
            continue
        return False
    return True


def _complete_framework_items(title: str, items: list[str]) -> list[str]:
    """If this looks like a 5Rs card, ensure all five Rs are present."""
    out = [str(x).strip() for x in (items or []) if str(x).strip()]
    blob = f"{title or ''} " + " ".join(out)
    looks_five = bool(re.search(r"\b5\s*R|five\s*R|5Rs\b", blob, re.I))
    hit = sum(1 for r in _FIVE_RS if re.search(rf"\b{re.escape(r)}\b", blob, re.I))
    if not (looks_five or hit >= 2):
        return out
    # Preserve existing wording; append any missing R as a plain label.
    lower_blob = " ".join(out).lower()
    for r in _FIVE_RS:
        if r.lower() not in lower_blob:
            out.append(r)
            lower_blob = " ".join(out).lower()
    return out


def _build_overlay_entries(
    segment: dict[str, Any],
    durations: dict[str, int],
    speaker_color_a: str,
    speaker_color_b: str,
) -> list[dict[str, Any]]:
    """Resolve a segment's `overlays` array to per-overlay ms windows.

    Each overlay's `show_at_line` / `hide_at_line` are 1-based line indices
    within this segment. We walk the segment's lines once to compute the
    start_ms of every line (segment-relative), then map each overlay's
    indices to absolute show_ms / hide_ms relative to the segment's start.

    The dominant speaker across the overlay's window picks the speaker
    color. If indices are out of range we clamp; if the overlay is malformed
    we drop it.
    """
    overlays_in = segment.get("overlays") or []
    segment_text = _segment_dialogue_blob(segment)
    if not overlays_in:
        return []

    lines = segment.get("lines") or []
    if not lines:
        return []

    # Per-line start (segment-relative) and which host owns it.
    line_starts: list[int] = []
    line_ends: list[int] = []
    line_hosts: list[str] = []
    cursor = 0
    for line in lines:
        lid = line.get("line_id")
        clip_ms = int(durations.get(lid, 0))
        line_starts.append(cursor)
        line_ends.append(cursor + clip_ms)
        line_hosts.append(line.get("host_id") or "A")
        cursor += clip_ms
        cursor += int(line.get("pause_after_ms") or 0)
    seg_end_ms = cursor

    n_lines = len(lines)
    out: list[dict[str, Any]] = []
    for ov in overlays_in:
        if not isinstance(ov, dict):
            continue
        t = ov.get("type")
        if t not in ("bullets", "stat", "chart"):
            continue
        try:
            show_idx = int(ov.get("show_at_line", 1))
            hide_idx = int(ov.get("hide_at_line", show_idx + 1))
        except (TypeError, ValueError):
            continue
        # 1-based → 0-based, clamped to the segment's line range.
        show_i = max(0, min(n_lines - 1, show_idx - 1))
        hide_i = max(0, min(n_lines - 1, hide_idx - 1))
        if hide_i <= show_i:
            hide_i = min(n_lines - 1, show_i + 1)
        show_ms = line_starts[show_i]
        # hide at the END of the hide line so the overlay covers that line.
        hide_ms = line_ends[hide_i]
        if hide_ms <= show_ms:
            hide_ms = min(seg_end_ms, show_ms + 1500)

        # Speaker color = dominant host across [show_i, hide_i].
        host_counts: dict[str, int] = {"A": 0, "B": 0}
        for i in range(show_i, hide_i + 1):
            h = line_hosts[i]
            if h in host_counts:
                host_counts[h] += 1
        dominant = "A" if host_counts["A"] >= host_counts["B"] else "B"
        speaker_color = speaker_color_a if dominant == "A" else speaker_color_b

        entry: dict[str, Any] = {
            "type": t,
            "show_ms": int(show_ms),
            "hide_ms": int(hide_ms),
            "speakerColor": speaker_color,
        }
        if t == "bullets":
            entry["title"] = str(ov.get("title") or "")
            items = ov.get("items") or []
            if isinstance(items, list):
                entry["items"] = _complete_framework_items(
                    str(entry.get("title") or ov.get("title") or ""),
                    [str(x) for x in items if x],
                )
            else:
                entry["items"] = []
        elif t == "stat":
            entry["label"] = str(ov.get("label") or "")
            entry["value"] = str(ov.get("value") or "")
            if ov.get("subtext"):
                entry["subtext"] = str(ov["subtext"])
        elif t == "chart":
            entry["title"] = str(ov.get("title") or "")
            entry["chart_type"] = str(ov.get("chart_type") or "growth")
            dps = ov.get("data_points") or []
            cleaned_dps: list[dict[str, Any]] = []
            if isinstance(dps, list):
                for dp in dps:
                    if not isinstance(dp, dict):
                        continue
                    try:
                        cleaned_dps.append({
                            "label": str(dp.get("label") or ""),
                            "value": float(dp.get("value") or 0),
                        })
                    except (TypeError, ValueError):
                        continue
            entry["data_points"] = cleaned_dps

        # Drop invented / ungrounded stat overlays (approved): digits must appear in dialogue.
        if entry.get("type") == "stat" and not _stat_value_grounded(str(entry.get("value") or ""), segment_text):
            continue
        out.append(entry)
    return out


def _host_names(episode: dict[str, Any]) -> tuple[str, str]:
    a_name = "Host A"
    b_name = "Host B"
    for h in episode.get("hosts") or []:
        if h.get("id") == "A" and h.get("name"):
            a_name = h["name"]
        elif h.get("id") == "B" and h.get("name"):
            b_name = h["name"]
    return a_name, b_name


_VAGUE_PEXELS = re.compile(
    # Ban lone fluff nouns (approved daily-loop): empty/team/office/project/data/etc.
    r"^(?:ai|a\.i\.?|artificial intelligence|tech|technology|future|business|innovation|digital|"
    r"empty|team|office|project|data|people|work|meeting|corporate|abstract)"
    r"(?:\s+(?:for|and|in|of|the|ai|tech))?$",
    re.IGNORECASE,
)


def _ground_pexels_query(query: str, fallback: str = "office collaboration meeting") -> str:
    q = (query or "").strip()
    q = re.sub(r"\s+", " ", q)
    if (not q) or _VAGUE_PEXELS.match(q) or q.lower() in {"ai", "ai for", "artificial intelligence", "empty", "team", "office", "project", "data", "people", "work", "meeting"}:
        return fallback
    q2 = re.sub(r"^(?:ai|artificial intelligence)\s+", "", q, flags=re.IGNORECASE).strip()
    return q2 or fallback


def fetch_pexels_photo(
    query: str,
    orientation: str = "landscape",
    *,
    pexels_key: str | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Look up a relevant landscape photo on Pexels.

    Returns `{url, photographer}` or None. Caches by query in
    `<run_dir>/pexels_cache.json`. Uses CDN URLs directly — no local download.
    """
    key = pexels_key or os.environ.get("PEXELS_API_KEY")
    if not key or not query.strip():
        return None

    cache_path = (run_dir / "pexels_cache.json") if run_dir else None
    cache: dict[str, Any] = {}
    if cache_path and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}

    cache_key = f"{query}|{orientation}".lower()
    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("url"), str) and cached["url"].startswith(("http://", "https://")):
        return cached

    try:
        import requests
    except ImportError:
        print("[visual_queue] pexels: requests not installed; skipping", file=sys.stderr)
        return None

    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": key},
            params={"query": query, "per_page": 5, "orientation": orientation},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        print(f"[visual_queue] pexels search failed: {exc}", file=sys.stderr)
        return None

    photos = payload.get("photos") or []
    if not photos:
        return None
    photo = photos[0]
    src = (photo.get("src") or {}).get("large2x") or (photo.get("src") or {}).get("large")
    if not src:
        return None
    entry = {
        "url": src,
        "photographer": photo.get("photographer") or "Pexels",
    }
    cache[cache_key] = entry
    if cache_path:
        try:
            cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        except OSError:
            pass
    return entry


# Target window length (ms) for one Gemini-chosen background image. ~25s is
# long enough for a coherent topic but short enough that the visuals keep
# moving with the conversation.
GRAPHICS_WINDOW_MS = 12_000  # approved B-roll ~10-15s


def build_graphics_manifest(
    full_transcript_words: list[dict[str, Any]],
    episode: dict[str, Any],
    run_dir: Path,
    pexels_key: str | None,
    mcp_client: Any = None,
) -> list[dict[str, Any]]:
    """Place context-aware Pexels images on the timeline.

    Walks `full_transcript_words` (whisper-aligned, absolute ms), chunks into
    ~25s windows, asks Gemini for a 2-4 word Pexels query per window, fetches
    a landscape photo, and emits one graphic entry per window with absolute
    `start_ms` / `end_ms` / `photo_url` / `query`.

    Results are cached at `<run_dir>/graphics_manifest.json` so re-renders are
    free.
    """
    cache_path = run_dir / "graphics_manifest.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, list):
                return cached
        except (OSError, json.JSONDecodeError):
            pass

    if not full_transcript_words or not pexels_key or mcp_client is None:
        return []

    # Build ~25s windows. Walk in time order, flushing whenever the window
    # would exceed GRAPHICS_WINDOW_MS.
    windows: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    window_start = int(full_transcript_words[0].get("start_ms", 0))
    for w in full_transcript_words:
        start_ms = int(w.get("start_ms", 0))
        if current and (start_ms - window_start) >= GRAPHICS_WINDOW_MS:
            windows.append(current)
            current = []
            window_start = start_ms
        current.append(w)
    if current:
        windows.append(current)

    graphics: list[dict[str, Any]] = []
    for idx, window in enumerate(windows, 1):
        text = " ".join((w.get("word") or "").strip() for w in window if w.get("word")).strip()
        if not text:
            continue
        try:
            resp = mcp_client.call("gemini_image_query", {"excerpt": text})
        except Exception as exc:
            print(f"[graphics] window {idx}: gemini call failed: {exc}", file=sys.stderr)
            continue
        if not isinstance(resp, dict):
            continue
        query = _ground_pexels_query((resp.get("query") or "").strip(), fallback="team workshop whiteboard")
        if not query or resp.get("gemini_failed"):
            print(
                f"[graphics] window {idx}: no query ({resp.get('warning') or 'empty'})",
                file=sys.stderr,
            )
            continue
        photo = fetch_pexels_photo(query, "landscape", pexels_key=pexels_key, run_dir=run_dir)
        if not photo:
            print(f"[graphics] window {idx}: pexels miss for {query!r}", file=sys.stderr)
            continue
        graphics.append({
            "start_ms": int(window[0].get("start_ms", 0)),
            "end_ms": int(window[-1].get("end_ms", 0)),
            "photo_url": photo["url"],
            "query": query,
        })
        print(f"[graphics] window {idx}: {query!r} → {photo['url']}", file=sys.stderr)

    try:
        cache_path.write_text(json.dumps(graphics, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[graphics] warning: failed to write cache: {exc}", file=sys.stderr)
    return graphics


def build_visual_manifest(
    episode: dict[str, Any],
    clip_durations: Any,
    run_dir: Path,
    pexels_key: str | None = None,
    audio_path: Path | str | None = None,
    mcp_client: Any = None,
) -> dict[str, Any]:
    """Build the Segment Blocks visual manifest.

    Produces (in order):
      - TitleCard cold-open (5s)
      - one SegmentCard per segment (timing from measured audio)
      - TitleCard outro (5s) at the tail

    Writes the result to `<run_dir>/visual_manifest.json` and returns it.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    # Default to the run's podcast.wav so callers don't have to thread it
    # through; the file exists during video build and gets removed only after
    # MP3 conversion. compute_word_timings silently falls back if it's gone.
    if audio_path is None:
        for cand in ("podcast.wav", "podcast.mp3"):
            p = run_dir / cand
            if p.exists():
                audio_path = p
                break

    seg_timings = compute_segment_timings(episode, clip_durations)
    word_timings = compute_word_timings(episode, clip_durations, audio_path=audio_path)
    # Pre-load durations once for per-segment overlay ms resolution.
    durations_map = _load_clip_durations(clip_durations)
    # Context-aware center graphics. Driven by the same word list, so it falls
    # back to [] when whisper alignment is unavailable or pexels/gemini aren't
    # configured.
    graphics_manifest = build_graphics_manifest(
        word_timings,
        episode,
        run_dir,
        pexels_key=pexels_key,
        mcp_client=mcp_client,
    )
    if not seg_timings:
        manifest = {"total_duration_ms": 0, "visuals": []}
        (run_dir / "visual_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return manifest

    total_ms = seg_timings[-1]["end_ms"]
    # Voice-coordinated names + colors. Same voice → same name+color, every run.
    host_a, host_b = _host_personas(episode)
    a_name = host_a["name"]
    b_name = host_b["name"]
    a_color = host_a["color"]
    b_color = host_b["color"]
    topic = (episode.get("topic") or "").strip()

    visuals: list[dict[str, Any]] = []

    # 1. Cold-open title card (5s at the very start, overlapping the first
    #    segment's audio).
    visuals.append({
        "visual_id": "open_title",
        "template": "TitleCard",
        "start_ms": 0,
        "end_ms": TITLE_CARD_MS,
        "data": {
            "title": topic or "PROJECT GAKU",
            "subtitle": "PROJECT GAKU",
        },
    })

    # 2. One SegmentCard per segment.
    segments = episode.get("segments") or []
    for i, (seg, timing) in enumerate(zip(segments, seg_timings), 1):
        seg_title = _clean_segment_title((seg.get("title") or f"Segment {i}").strip())
        bullets = _extract_bullets(seg)
        speaker = _current_speaker(seg)
        seg_idx = i - 1

        photo_url = None
        if pexels_key:
            query = _ground_pexels_query(f"{seg_title}".strip(), fallback="enterprise team planning session")
            photo = fetch_pexels_photo(query, "landscape", pexels_key=pexels_key, run_dir=run_dir)
            if photo:
                photo_url = photo["url"]

        # SegmentCard runs for the full segment. The opening/closing title
        # cards crossfade over the start/end during composition.
        start_ms = timing["start_ms"]
        end_ms = timing["end_ms"]
        if i == 1:
            # First segment: let the cold-open title card fully play, then
            # cut in. Avoids both cards fighting for the same frame.
            start_ms = TITLE_CARD_MS
        if i == len(segments):
            # Last segment: stop 5s before the end to make room for the outro.
            end_ms = max(start_ms + 1000, total_ms - TITLE_CARD_MS)

        # Filter the global word list down to this segment's words, rebase
        # timestamps to the SegmentCard's start_ms (segment 1 is offset by the
        # cold-open title card), then group into sentences for one-at-a-time
        # subtitle display.
        seg_words = [
            {
                "word": w["word"],
                "speaker": w["speaker"],
                "start_ms": w["start_ms"] - start_ms,
                "end_ms": w["end_ms"] - start_ms,
            }
            for w in word_timings
            if w["segment_idx"] == seg_idx
        ]
        sentence_timings = group_into_sentences(seg_words)

        # Per-segment overlays (bullets / stat / chart). Timing comes from the
        # segment's own line indices, then we shift to the SegmentCard frame by
        # offsetting against `start_ms` — the first segment runs after the cold
        # open, so the SegmentCard starts at TITLE_CARD_MS on the global
        # timeline but t=0 inside the card itself, which is what we already
        # produce (line_starts are segment-relative).
        seg_overlays = _build_overlay_entries(
            seg,
            durations_map,
            speaker_color_a=a_color,
            speaker_color_b=b_color,
        )

        # Graphics that overlap this segment's absolute time range, rebased.
        seg_graphics = []
        for g in graphics_manifest:
            g_start = int(g.get("start_ms", 0))
            g_end = int(g.get("end_ms", 0))
            # Overlap test against [start_ms, end_ms) on the absolute timeline.
            if g_end <= start_ms or g_start >= end_ms:
                continue
            seg_graphics.append({
                "start_ms": g_start - start_ms,
                "end_ms": g_end - start_ms,
                "photo_url": g.get("photo_url") or "",
                "query": g.get("query") or "",
            })

        visuals.append({
            "visual_id": f"segment_{i:02d}",
            "template": "SegmentCard",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "data": {
                "title": seg_title,
                "bullets": bullets,
                "photo_url": photo_url or "",
                "host_a_name": a_name,
                "host_b_name": b_name,
                "host_a_color": a_color,
                "host_b_color": b_color,
                "current_speaker": speaker,
                "segment_number": i,
                "sentence_timings": sentence_timings,
                "graphics": seg_graphics,
                "overlays": seg_overlays,
            },
        })

    # 3. Outro title card.
    visuals.append({
        "visual_id": "close_title",
        "template": "TitleCard",
        "start_ms": max(0, total_ms - TITLE_CARD_MS),
        "end_ms": total_ms,
        "data": {
            "title": "PROJECT GAKU",
            "subtitle": topic or "Thanks for listening",
        },
    })

    manifest = {
        "total_duration_ms": total_ms,
        "segment_timings": seg_timings,
        "visuals": visuals,
    }
    (run_dir / "visual_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


__all__ = [
    "compute_segment_timings",
    "compute_word_timings",
    "group_into_sentences",
    "build_graphics_manifest",
    "build_visual_manifest",
    "fetch_pexels_photo",
]
