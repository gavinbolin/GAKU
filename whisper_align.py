"""GPU-accelerated forced alignment for podcast karaoke subtitles.

Wraps faster-whisper's built-in word_timestamps to produce per-word
[start_ms, end_ms] offsets relative to the audio file's start. For clean
TTS audio (Kokoro) this is as accurate as WhisperX's wav2vec2 alignment;
WhisperX itself can't install cleanly on Python 3.14 today because its
pinned faster-whisper==1.0.0 has no 3.14 wheels.

Public API:
    align(audio_path, language="en") -> list[dict] with keys:
        word     (str)  — token as Whisper emitted it (leading space preserved)
        start_ms (int)  — relative to audio start
        end_ms   (int)  — relative to audio start
        prob     (float) optional confidence

Results are cached as <audio>.whisper_words.json next to the audio so
re-runs of the same clip are free. The model is loaded once per process.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

# Lazy globals: we don't import torch/faster_whisper until the first
# alignment call so that just `import whisper_align` from visual_queue
# costs nothing when alignment ends up disabled or unused.
_model_lock = threading.Lock()
_model = None
_model_device: str | None = None
_model_compute: str | None = None

# Override with WHISPER_ALIGN_MODEL=small.en for faster runs, base.en is the
# default sweet spot — ~10x realtime on a 2070 SUPER, accurate on TTS.
_MODEL_NAME = os.environ.get("WHISPER_ALIGN_MODEL", "base.en")


def _device_and_compute() -> tuple[str, str]:
    """Pick GPU + float16 when CUDA is available, else CPU + int8."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", "float16"
    except ImportError:
        pass
    return "cpu", "int8"


def _get_model():
    global _model, _model_device, _model_compute
    if _model is not None:
        return _model, _model_device
    with _model_lock:
        if _model is not None:
            return _model, _model_device
        from faster_whisper import WhisperModel
        device, compute = _device_and_compute()
        print(
            f"[whisper_align] loading {_MODEL_NAME} on {device}/{compute}",
            file=sys.stderr,
        )
        _model = WhisperModel(_MODEL_NAME, device=device, compute_type=compute)
        _model_device = device
        _model_compute = compute
    return _model, _model_device


def _cache_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(audio_path.suffix + ".whisper_words.json")


def _load_cached(cache_path: Path, audio_path: Path) -> list[dict[str, Any]] | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # Invalidate if the audio is newer than the cache (clip got regenerated).
    try:
        if cache_path.stat().st_mtime < audio_path.stat().st_mtime:
            return None
    except OSError:
        return None
    words = payload.get("words")
    if isinstance(words, list):
        return words
    return None


def _save_cache(cache_path: Path, words: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    try:
        cache_path.write_text(
            json.dumps({"meta": meta, "words": words}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[whisper_align] warn: could not write cache {cache_path}: {exc}", file=sys.stderr)


def align(
    audio_path: str | Path,
    language: str = "en",
    *,
    initial_prompt: str | None = None,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Transcribe + word-time `audio_path`.

    Returns a list of `{word, start_ms, end_ms, prob}` dicts in playback order.
    Returns [] on failure — callers should fall back to proportional timing.
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        print(f"[whisper_align] missing audio: {audio_path}", file=sys.stderr)
        return []

    cache_path = _cache_path(audio_path)
    if use_cache:
        cached = _load_cached(cache_path, audio_path)
        if cached is not None:
            return cached

    try:
        model, device = _get_model()
    except Exception as exc:
        print(f"[whisper_align] model load failed: {exc!r}", file=sys.stderr)
        return []

    try:
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=False,
            beam_size=1,
            condition_on_previous_text=False,
            initial_prompt=initial_prompt,
        )
        # segments is a generator — iterate to materialize.
        words: list[dict[str, Any]] = []
        for seg in segments:
            for w in (seg.words or []):
                start = float(w.start or 0.0)
                end = float(w.end or start)
                if end < start:
                    end = start
                words.append({
                    "word": (w.word or "").strip(),
                    "start_ms": int(round(start * 1000)),
                    "end_ms": int(round(end * 1000)),
                    "prob": float(getattr(w, "probability", 0.0) or 0.0),
                })
    except Exception as exc:
        print(f"[whisper_align] transcribe failed for {audio_path.name}: {exc!r}", file=sys.stderr)
        return []

    if use_cache and words:
        _save_cache(cache_path, words, {
            "model": _MODEL_NAME,
            "device": _model_device,
            "language": language,
        })
    return words


# Compatibility alias matching the user's original WhisperX-style naming.
def align_audio_clip(audio_path: str | Path, language: str = "en") -> list[dict[str, Any]]:
    return align(audio_path, language=language)


__all__ = ["align", "align_audio_clip"]


if __name__ == "__main__":
    # Quick CLI: python whisper_align.py path/to/audio.wav
    if len(sys.argv) < 2:
        print("usage: python whisper_align.py <audio_path> [language]", file=sys.stderr)
        raise SystemExit(2)
    p = Path(sys.argv[1])
    lang = sys.argv[2] if len(sys.argv) > 2 else "en"
    out = align(p, language=lang)
    print(json.dumps(out[:20], indent=2, ensure_ascii=False))
    print(f"... ({len(out)} words total)", file=sys.stderr)
