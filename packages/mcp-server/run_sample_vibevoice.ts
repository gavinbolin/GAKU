// Short VibeVoice validation sample driver.
//
//   Stage 1 (fast, Gemini):   npx tsx run_sample_vibevoice.ts --stage script
//     → generates a "solar vs nuclear" script, carves the opening ~2.5 min into
//       a single-segment sample episode, remaps hosts to Serana([1]) + male([2]),
//       writes output/vibevoice_sample/sample_episode.json
//
//   Stage 2 (slow, GPU):      TTS_BACKEND=vibevoice npx tsx run_sample_vibevoice.ts --stage synth
//     → generate_audio (VibeVoice one-pass + whisper align + slice) →
//       concatenate_audio_track → reports per-line durations + sample wav path.

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

import { synthesizeResearchToScript } from "./src/tools/synthesize_research_to_script.js";
import { validateEpisodeJson } from "./src/tools/validate_episode_json.js";
import { generateAudio } from "./src/tools/generate_audio.js";
import { concatenateAudioTrack } from "./src/tools/concatenate_audio_track.js";
import { alignAndSliceSegment } from "./src/tts/vibevoiceBackend.js";

const PROJECT_ROOT = resolve(import.meta.dirname, "../../..");
const SAMPLE_DIR = join(PROJECT_ROOT, "output", "vibevoice_sample");
const EPISODE_PATH = join(SAMPLE_DIR, "sample_episode.json");
const FULL_PATH = join(SAMPLE_DIR, "full_episode.json");

const TOPIC = "The pros and cons of solar vs nuclear";
const SAMPLE_WORD_BUDGET = 420; // ~2.5 min @ 165 wpm
const MIN_SAMPLE_LINES = 8;

// Serana leads (speaker [1]); the male synthetic voice is secondary ([2]).
const HOSTS = [
  { id: "A", name: "Serana", voice_id: "Serana_ref.wav" },
  { id: "B", name: "Cole", voice_id: "host_b.wav" },
];

function loadDotenv() {
  for (const p of [join(PROJECT_ROOT, ".env"), join(PROJECT_ROOT, "podcast-pipeline", ".env")]) {
    if (!existsSync(p)) continue;
    for (const raw of readFileSync(p, "utf-8").split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq < 0) continue;
      const k = line.slice(0, eq).trim();
      const v = line.slice(eq + 1).trim().replace(/^["']|["']$/g, "");
      if (!(k in process.env)) process.env[k] = v;
    }
  }
}

function wordCount(s: string): number {
  return s.trim().split(/\s+/).filter(Boolean).length;
}

async function stageScript() {
  loadDotenv();
  mkdirSync(SAMPLE_DIR, { recursive: true });

  const research = [
    "Solar: falling PV/module and utility-scale costs; near-zero marginal cost; ",
    "intermittent (day/weather), needs storage or firming; land/materials footprint; ",
    "fast to deploy; distributed rooftop option.",
    "Nuclear: very high capacity factor and firm baseload; low lifecycle CO2; ",
    "high upfront capex and long build times; waste storage and public-perception issues; ",
    "SMRs as a newer path; land-efficient and dense energy.",
    "Comparison angles: cost per MWh trends, reliability/firmness, land use, ",
    "safety record vs perception, build speed, grid role (baseload vs variable).",
  ].join("");

  process.stderr.write(`\n=== stage: script (Gemini) — "${TOPIC}" ===\n`);
  const res = await synthesizeResearchToScript({
    topic: TOPIC,
    research_notes: research,
    target_duration_minutes: 8,
    tone: "Conversational, friendly, a little playful",
    audience_level: "Intermediate",
  });
  if (!res.episode) {
    throw new Error(`script generation failed: ${res.warning ?? "unknown"}`);
  }
  writeFileSync(FULL_PATH, JSON.stringify(res.episode, null, 2));
  process.stderr.write(`full script saved: ${FULL_PATH}\n`);

  // Flatten lines in playback order, carve the opening up to the word budget.
  const full = res.episode as any;
  const flat: Array<{ host_id: string; text: string }> = [];
  for (const seg of full.segments ?? []) {
    for (const ln of seg.lines ?? []) flat.push({ host_id: ln.host_id, text: String(ln.text) });
  }
  const sampleLines: Array<Record<string, unknown>> = [];
  let words = 0;
  for (let i = 0; i < flat.length; i++) {
    const hostRaw = String(flat[i].host_id).toUpperCase();
    const host_id = hostRaw === "A" || hostRaw === "B" ? hostRaw : i % 2 === 0 ? "A" : "B";
    sampleLines.push({
      line_id: `s01_opening_l${String(i + 1).padStart(2, "0")}`,
      host_id,
      text: flat[i].text,
      pause_after_ms: 250,
    });
    words += wordCount(flat[i].text);
    if (words >= SAMPLE_WORD_BUDGET && sampleLines.length >= MIN_SAMPLE_LINES) break;
  }

  const sample = {
    episode_id: "solar-vs-nuclear-sample",
    topic: TOPIC,
    target_duration_minutes: 5,
    hosts: HOSTS,
    segments: [{ segment_id: "s01_opening", title: "Opening", lines: sampleLines }],
  };

  const v = await validateEpisodeJson(sample);
  if (!v.valid) {
    process.stderr.write(JSON.stringify(v.errors, null, 2) + "\n");
    throw new Error("sample episode failed schema validation");
  }
  writeFileSync(EPISODE_PATH, JSON.stringify(sample, null, 2));

  const est = sampleLines.reduce((a, l) => a + wordCount(String((l as any).text)), 0);
  process.stderr.write(
    `\nsample: ${sampleLines.length} lines, ~${est} words, est ~${((est / 165) * 60).toFixed(0)}s audio\n` +
      `est VibeVoice compute ≈ ${(((est / 165) * 60) * 7 / 60).toFixed(1)} min @ 7x realtime\n` +
      `sample episode saved: ${EPISODE_PATH}\n`
  );
  process.stdout.write("SAMPLE_READY=" + JSON.stringify({ lines: sampleLines.length, est_words: est, path: EPISODE_PATH }) + "\n");
}

function probeDurationMs(path: string): number {
  const r = spawnSync("ffprobe", [
    "-v", "error", "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1", path,
  ], { encoding: "utf-8" });
  return Math.round(parseFloat((r.stdout || "0").trim()) * 1000);
}

async function stageSynth() {
  loadDotenv();
  const episode = JSON.parse(readFileSync(EPISODE_PATH, "utf-8"));
  const clipsDir = join(SAMPLE_DIR, "clips");
  const audioPath = join(SAMPLE_DIR, "sample.wav");

  process.stderr.write(`\n=== stage: synth (backend=${process.env.TTS_BACKEND ?? "kokoro"}) ===\n`);
  const t0 = Date.now();
  const { clips } = await generateAudio(episode, clipsDir);
  const genSec = (Date.now() - t0) / 1000;
  process.stderr.write(`\ngenerated ${clips.length} per-line clips in ${genSec.toFixed(1)}s\n`);

  const concat = await concatenateAudioTrack(clips as any, audioPath);
  const totalMs = probeDurationMs(audioPath);

  const perLine = clips.map((c) => ({ line_id: c.line_id, host_id: c.host_id, duration_ms: c.duration_ms }));
  const summary = {
    backend: process.env.TTS_BACKEND ?? "kokoro",
    sample_audio: audioPath,
    clip_count: concat.clip_count,
    total_duration_ms: totalMs,
    total_duration_readable: `${Math.floor(totalMs / 60000)}m ${Math.round((totalMs % 60000) / 1000)}s`,
    generation_seconds: Math.round(genSec),
    realtime_factor: totalMs > 0 ? +(genSec / (totalMs / 1000)).toFixed(2) : null,
    per_line: perLine,
  };
  writeFileSync(join(SAMPLE_DIR, "sample_summary.json"), JSON.stringify(summary, null, 2));
  process.stdout.write("\nSAMPLE_SUMMARY=" + JSON.stringify(summary) + "\n");
}

// Turn an ALREADY-GENERATED one-pass FLAC into per-line clips + a concatenated
// sample track, without regenerating. Used to salvage/validate the
// align→slice→concat half from a completed VibeVoice generation.
//   npx tsx run_sample_vibevoice.ts --stage resume --flac <path-to.flac>
async function stageResume() {
  const flacIdx = process.argv.indexOf("--flac");
  if (flacIdx < 0) throw new Error("resume requires --flac <path>");
  const flac = process.argv[flacIdx + 1];
  if (!existsSync(flac)) throw new Error(`FLAC not found: ${flac}`);

  const episode = JSON.parse(readFileSync(EPISODE_PATH, "utf-8"));
  const segment = episode.segments[0];
  const clipsDir = join(SAMPLE_DIR, "clips");
  const audioPath = join(SAMPLE_DIR, "sample.wav");

  process.stderr.write(`\n=== stage: resume (align+slice+concat from ${flac}) ===\n`);
  const t0 = Date.now();
  const clips = await alignAndSliceSegment(clipsDir, segment, flac);
  const concat = await concatenateAudioTrack(clips as any, audioPath);
  const totalMs = probeDurationMs(audioPath);
  const flacMs = probeDurationMs(flac);

  const perLine = clips.map((c) => ({ line_id: c.line_id, host_id: c.host_id, duration_ms: c.duration_ms }));
  const zero = perLine.filter((l) => l.duration_ms <= 0);
  const summary = {
    backend: "vibevoice",
    source_flac: flac,
    flac_duration_ms: flacMs,
    sample_audio: audioPath,
    clip_count: concat.clip_count,
    total_duration_ms: totalMs,
    total_duration_readable: `${Math.floor(totalMs / 60000)}m ${Math.round((totalMs % 60000) / 1000)}s`,
    align_slice_concat_seconds: Math.round((Date.now() - t0) / 1000),
    zero_length_lines: zero.length,
    per_line: perLine,
  };
  writeFileSync(join(SAMPLE_DIR, "sample_summary.json"), JSON.stringify(summary, null, 2));
  process.stdout.write("\nSAMPLE_SUMMARY=" + JSON.stringify(summary) + "\n");
}

async function main() {
  const stage = process.argv.includes("--stage")
    ? process.argv[process.argv.indexOf("--stage") + 1]
    : "script";
  if (stage === "script") await stageScript();
  else if (stage === "synth") await stageSynth();
  else if (stage === "resume") await stageResume();
  else throw new Error(`unknown --stage ${stage}`);
}

main().catch((e) => {
  console.error("SAMPLE FAILED:", e);
  process.exit(1);
});
