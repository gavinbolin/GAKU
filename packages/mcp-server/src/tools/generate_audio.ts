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
//
// 2026-09-25: TTS pronunciation lexicon (config/tts_lexicon.json). Respellings
// are applied to a DEEP COPY of the episode that only the TTS backend sees.
// The caller's episode object (and episode.json on disk) keeps the written
// spelling, so captions / karaoke / cards never show the respelling.
// visual_queue.compute_word_timings folds the respelled whisper tokens back.

import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createTtsBackend } from "../tts/index.js";
import type { AudioClip } from "../tts/TtsBackend.js";

export type { AudioClip };

interface LexiconEntry {
  written: string;
  spoken: string;
  backends: string[];
}

function lexiconPath(): string {
  if (process.env.GAKU_TTS_LEXICON) return process.env.GAKU_TTS_LEXICON;
  // packages/mcp-server/{src|dist}/tools -> podcast-pipeline/config
  const here = dirname(fileURLToPath(import.meta.url));
  return resolve(here, "..", "..", "..", "..", "config", "tts_lexicon.json");
}

export function loadLexicon(path: string = lexiconPath()): LexiconEntry[] {
  if (!existsSync(path)) return [];
  try {
    const data = JSON.parse(readFileSync(path, "utf8")) as { entries?: unknown[] };
    const out: LexiconEntry[] = [];
    for (const raw of data.entries ?? []) {
      const e = raw as Record<string, unknown>;
      const written = String(e.written ?? "").trim();
      const spoken = String(e.spoken ?? "").trim();
      if (!written || !spoken || written === spoken) continue;
      const backends = Array.isArray(e.backends)
        ? (e.backends as unknown[]).map((b) => String(b).toLowerCase())
        : [];
      out.push({ written, spoken, backends });
    }
    out.sort((a, b) => b.written.length - a.written.length);
    return out;
  } catch (err) {
    process.stderr.write(`[generate_audio] lexicon unreadable (${(err as Error).message}); ignoring\n`);
    return [];
  }
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function matchCase(src: string, repl: string): string {
  if (src.length > 1 && src === src.toUpperCase() && src !== src.toLowerCase()) return repl.toUpperCase();
  if (src[0] && src[0] === src[0].toUpperCase() && src[0] !== src[0].toLowerCase()) {
    return repl[0].toUpperCase() + repl.slice(1);
  }
  return repl.toLowerCase();
}

/** Same rules as tts_lexicon.apply_lexicon (whole word, case-preserving). */
export function applyLexicon(text: string, lexicon: LexiconEntry[], backend: string): string {
  let out = text ?? "";
  for (const e of lexicon) {
    if (e.backends.length > 0 && !e.backends.includes(backend)) continue;
    const re = new RegExp(`(?<![\\w-])${escapeRe(e.written)}(?![\\w])`, "gi");
    out = out.replace(re, (m) => matchCase(m, e.spoken));
  }
  return out;
}

/**
 * Strip markdown emphasis (*x*, **x**, _x_, `x`) so TTS never reads markers.
 * Mirrors podcast-pipeline/text_clean.py strip_markdown (words are kept).
 */
export function stripMarkdown(text: string): string {
  if (!text) return text ?? "";
  let s = text.replace(/`/g, "");
  if (!s.includes("*") && !s.includes("_")) return s;
  const emphasis = [
    /(?<![\w*])\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*(?![\w*])/g,
    /(?<![\w*])\*\*(?=\S)(.+?)(?<=\S)\*\*(?![\w*])/g,
    /(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])/g,
    /(?<![\w_])__(?=\S)(.+?)(?<=\S)__(?![\w_])/g,
    /(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])/g,
  ];
  for (const re of emphasis) s = s.replace(re, "$1");
  s = s.replace(/(?<!\S)[*_]+(?=\S)|(?<=\S)[*_]+(?!\S)/g, "");
  s = s.replace(/\*/g, "");
  return s.replace(/[ \t]{2,}/g, " ").trim();
}

function spokenCopy(episodeRaw: object, backend: string): { episode: object; changed: number } {
  const lexicon = loadLexicon();
  const copy = JSON.parse(JSON.stringify(episodeRaw)) as {
    segments?: { lines?: { text?: string }[] }[];
  };
  let changed = 0;
  for (const seg of copy.segments ?? []) {
    for (const line of seg.lines ?? []) {
      if (typeof line.text !== "string") continue;
      const spoken = applyLexicon(stripMarkdown(line.text), lexicon, backend);
      if (spoken !== line.text) {
        line.text = spoken;
        changed++;
      }
    }
  }
  return { episode: copy, changed };
}

export async function generateAudio(
  episodeRaw: object,
  outDir: string
): Promise<{ clips: AudioClip[] }> {
  const backend = createTtsBackend();
  process.stderr.write(`[generate_audio] backend=${backend.name}\n`);
  const { episode, changed } = spokenCopy(episodeRaw, String(backend.name).toLowerCase());
  if (changed > 0) {
    process.stderr.write(`[generate_audio] tts text normalized for ${changed} line(s) (lexicon/markdown; TTS input only)\n`);
  }
  return backend.generate(episode, outDir);
}
