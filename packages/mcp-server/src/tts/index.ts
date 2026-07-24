// TTS backend factory. Selected by the TTS_BACKEND env var; Kokoro is the
// default so existing runs are unchanged.
//
//   TTS_BACKEND=kokoro     (default) per-line HTTP synthesis, near-realtime
//   TTS_BACKEND=vibevoice  one-pass-per-segment dialogue via local ComfyUI (slow)

import type { TtsBackend } from "./TtsBackend.js";
import { KokoroBackend } from "./kokoroBackend.js";
import { VibeVoiceBackend } from "./vibevoiceBackend.js";

export function createTtsBackend(name?: string): TtsBackend {
  const backend = (name ?? process.env.TTS_BACKEND ?? "kokoro").toLowerCase();
  switch (backend) {
    case "vibevoice":
      return new VibeVoiceBackend();
    case "kokoro":
      return new KokoroBackend();
    default:
      throw new Error(`Unknown TTS_BACKEND="${backend}" (expected "kokoro" or "vibevoice")`);
  }
}

export type { TtsBackend, AudioClip } from "./TtsBackend.js";
