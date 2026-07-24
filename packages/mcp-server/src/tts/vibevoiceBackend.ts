// VibeVoiceBackend — one-pass-per-segment two-speaker dialogue.
//
// Per segment:
//   1. Build the tagged dialogue ([1]:/[2]: per line, mapped from host order)
//      and generate the WHOLE segment in a single VibeVoice pass (real two-host
//      chemistry — the reason to use VibeVoice at all).
//   2. Forced-align the segment audio to the KNOWN ordered line texts via
//      align_lines.py (faster-whisper) to recover per-line time boundaries.
//   3. Slice the segment audio into per-line WAVs at those boundaries
//      (24 kHz mono pcm_s16le, matching concatenate_audio_track's canonical
//      format), pause_after_ms = 0 (VibeVoice already bakes in natural pauses).
//
// Output is the SAME AudioClip[] shape Kokoro produces, so concatenate /
// compute_visual_timings / compose all run unchanged.
//
// Voice mapping: episode.hosts order → speaker number ([1] = hosts[0]), and
// each host's `voice_id` is the reference-clip FILENAME as it appears in
// ComfyUI's input (voices) dir, e.g. "Serana_ref.wav". GPU is single-tenant:
// the caller must ensure Wan/AssetForge are idle before this runs.

import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

import type { Episode, Segment } from "../../../schema/types.js";
import type { AudioClip, TtsBackend } from "./TtsBackend.js";
import { VibeVoiceClient } from "../vibevoice/vibevoiceClient.js";

const SAMPLE_RATE = 24000;

interface LineBoundary {
  line_id: string;
  start_ms: number;
  end_ms: number;
  duration_ms: number;
  matched_words: number;
}

export class VibeVoiceBackend implements TtsBackend {
  readonly name = "vibevoice";

  async generate(episodeRaw: object, outDir: string): Promise<{ clips: AudioClip[] }> {
    const episode = episodeRaw as Episode;
    await mkdir(outDir, { recursive: true });

    if (episode.hosts.length !== 2) {
      throw new Error(
        `VibeVoiceBackend currently supports exactly 2 hosts (the two-speaker ` +
          `workflow); got ${episode.hosts.length}.`
      );
    }

    // host_id -> speaker number ([1] = hosts[0], [2] = hosts[1]).
    const speakerNumByHost = new Map<string, number>();
    episode.hosts.forEach((h, i) => speakerNumByHost.set(h.id, i + 1));
    const speaker1Clip = episode.hosts[0].voice_id;
    const speaker2Clip = episode.hosts[1].voice_id;

    const client = new VibeVoiceClient({
      onProgress: (s) =>
        process.stderr.write(`[vibevoice]   ...${s.toFixed(0)}s elapsed (generating)\n`),
    });

    const clips: AudioClip[] = [];

    for (const segment of episode.segments) {
      const taggedText = buildTaggedDialogue(segment, speakerNumByHost);
      process.stderr.write(
        `[vibevoice] segment ${segment.segment_id}: one-pass generating ` +
          `${segment.lines.length} lines...\n`
      );

      const gen = await client.generate({
        taggedText,
        speaker1Clip,
        speaker2Clip,
        filenamePrefix: `VibeVoice/gaku_${episode.episode_id}_${segment.segment_id}`,
      });
      process.stderr.write(
        `[vibevoice] segment ${segment.segment_id}: generated ${gen.filename} ` +
          `in ${gen.runtimeSec.toFixed(1)}s\n`
      );

      // Recover per-line boundaries + slice into per-line WAVs. Shared with the
      // resume path so the exact shipping code is exercised on an existing FLAC.
      const segClips = await alignAndSliceSegment(outDir, segment, gen.audioPath);
      clips.push(...segClips);
    }

    return { clips };
  }
}

/**
 * Forced-align a one-pass segment audio file to its known ordered line texts and
 * slice it into per-line WAVs (24 kHz mono, pause_after_ms=0). Returns the
 * per-line AudioClip[] for this segment. Reusable outside the generate loop so a
 * completed FLAC can be turned into clips without regenerating.
 */
export async function alignAndSliceSegment(
  outDir: string,
  segment: Segment,
  audioPath: string
): Promise<AudioClip[]> {
  await mkdir(outDir, { recursive: true });
  const boundaries = await alignLines(outDir, segment, audioPath);

  const clips: AudioClip[] = [];
  for (const b of boundaries) {
    const outPath = join(outDir, `${b.line_id}.wav`);
    await sliceClip(audioPath, b.start_ms, b.end_ms, outPath);
    const line = segment.lines.find((l) => l.line_id === b.line_id)!;
    clips.push({
      line_id: b.line_id,
      host_id: line.host_id,
      path: outPath,
      duration_ms: b.duration_ms,
      pause_after_ms: 0, // pauses already baked into the one-pass audio
    });
  }

  const weak = boundaries.filter((b) => b.matched_words === 0).length;
  if (weak > 0) {
    process.stderr.write(
      `[vibevoice] segment ${segment.segment_id}: ${weak}/${boundaries.length} ` +
        `lines had no whisper word match (boundary carried from previous line)\n`
    );
  }
  return clips;
}

function buildTaggedDialogue(
  segment: Segment,
  speakerNumByHost: Map<string, number>
): string {
  return segment.lines
    .map((line) => {
      const n = speakerNumByHost.get(line.host_id);
      if (!n) throw new Error(`No speaker number for host_id=${line.host_id} on ${line.line_id}`);
      // Collapse newlines so tags stay line-leading (VibeVoice splits on [n]:).
      const text = line.text.replace(/\s+/g, " ").trim();
      return `[${n}]: ${text}`;
    })
    .join("\n");
}

function pipelineRoot(): string {
  // src/vibevoice → ... → podcast-pipeline (where align_lines.py lives).
  const here = dirname(fileURLToPath(import.meta.url));
  return resolve(here, "..", "..", "..", "..");
}

async function alignLines(
  outDir: string,
  segment: Segment,
  audioPath: string
): Promise<LineBoundary[]> {
  const root = pipelineRoot();
  const linesPath = join(outDir, `${segment.segment_id}.lines.json`);
  const boundariesPath = join(outDir, `${segment.segment_id}.boundaries.json`);
  await writeFile(
    linesPath,
    JSON.stringify(segment.lines.map((l) => ({ line_id: l.line_id, text: l.text }))),
    "utf-8"
  );

  const python = process.env.PYTHON ?? "python";
  await runProc(
    python,
    [join(root, "align_lines.py"), "--audio", audioPath, "--lines", linesPath, "--out", boundariesPath],
    root
  );

  const { readFile } = await import("node:fs/promises");
  const parsed = JSON.parse(await readFile(boundariesPath, "utf-8")) as {
    lines: LineBoundary[];
  };
  return parsed.lines;
}

function sliceClip(
  srcPath: string,
  startMs: number,
  endMs: number,
  outPath: string
): Promise<void> {
  const ss = (startMs / 1000).toFixed(3);
  const to = (endMs / 1000).toFixed(3);
  // Output-side -ss/-to with re-encode = sample-accurate cut. Canonical format
  // (24 kHz mono pcm_s16le) matches concatenate_audio_track so the joins are clean.
  return runFfmpeg([
    "-y", "-i", srcPath,
    "-ss", ss, "-to", to,
    "-ar", String(SAMPLE_RATE), "-ac", "1", "-acodec", "pcm_s16le",
    outPath,
  ]);
}

function runFfmpeg(args: string[]): Promise<void> {
  return runProc("ffmpeg", ["-nostdin", "-loglevel", "error", ...args]);
}

function runProc(cmd: string, args: string[], cwd?: string): Promise<void> {
  return new Promise((resolveP, rejectP) => {
    const proc = spawn(cmd, args, { cwd, stdio: ["ignore", "pipe", "inherit"] });
    proc.stdout?.on("data", (chunk) => process.stderr.write(chunk));
    proc.on("error", rejectP);
    proc.on("close", (code) => {
      if (code === 0) resolveP();
      else rejectP(new Error(`${cmd} exited ${code}`));
    });
  });
}
