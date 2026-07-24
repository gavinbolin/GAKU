// generate_audio
// Synthesize per-line audio for an episode and return AudioClip[] =
//   [{ line_id, host_id, path, duration_ms, pause_after_ms }].
//
// This is now a thin dispatcher over a selectable TTS backend
// (TTS_BACKEND=kokoro|vibevoice, default kokoro). The Kokoro path is unchanged:
//   - per-line Kokoro HTTP synthesis -> per-line WAV -> ffprobe real duration.
// The VibeVoice path does one-pass-per-segment dialogue + whisper alignment and
// returns the identical AudioClip[] shape, so everything downstream
// (concatenate_audio_track, compute_visual_timings, compose_final_video) is
// backend-agnostic and needs no changes.
//
// duration_ms is the REAL clip length. For Kokoro it's probed with ffprobe after
// writing the WAV; for VibeVoice it's the aligned per-line span. Using a
// word-count estimate would let overlays drift seconds out of sync over a
// multi-minute podcast.

import { createTtsBackend } from "../tts/index.js";
import type { AudioClip } from "../tts/TtsBackend.js";

export type { AudioClip };

export async function generateAudio(
  episodeRaw: object,
  outDir: string
): Promise<{ clips: AudioClip[] }> {
  const backend = createTtsBackend();
  process.stderr.write(`[generate_audio] backend=${backend.name}\n`);
  return backend.generate(episodeRaw, outDir);
}
