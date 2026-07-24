// TtsBackend — the pluggable text-to-speech seam for PROJECT GAKU.
//
// Both backends take a whole episode and return the SAME per-line AudioClip[]
// contract, so everything downstream (concatenate_audio_track,
// compute_visual_timings, compose_final_video) is backend-agnostic and unchanged.
//
//   - KokoroBackend   : per-line HTTP synthesis (fast, near-realtime). DEFAULT.
//   - VibeVoiceBackend: one-pass-per-segment dialogue on a local ComfyUI server
//                       (slow, ~7s compute per 1s audio) with real two-host
//                       chemistry, then whisper forced-alignment to recover the
//                       per-line boundaries GAKU's visual timing depends on.
//
// The abstraction sits at the WHOLE-EPISODE level (not per-line synthesize())
// on purpose: VibeVoice generates a whole tagged segment in a single pass, so it
// cannot live behind a per-line call.

export interface AudioClip {
  line_id: string;
  host_id: string;
  path: string;
  duration_ms: number;
  pause_after_ms: number;
}

export interface TtsBackend {
  /** Human-readable backend name, for logs. */
  readonly name: string;
  /**
   * Synthesize every line of `episode` into per-line WAV files under `outDir`
   * and return the resulting clip metadata in playback order.
   */
  generate(episodeRaw: object, outDir: string): Promise<{ clips: AudioClip[] }>;
}
