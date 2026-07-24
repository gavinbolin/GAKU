// One-off orchestrator that drives the PROJECT GAKU pipeline end-to-end for the
// QuickBooks episode. Runs each MCP tool function directly (via tsx) in the
// documented order:
//   validate -> generate_audio -> concatenate_audio_track ->
//   compute_visual_timings -> render_visual (each) -> compose_final_video
//
// Run:  npx tsx run_pipeline.ts

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join, resolve } from "node:path";

import { validateEpisodeJson } from "./src/tools/validate_episode_json.js";
import { generateAudio } from "./src/tools/generate_audio.js";
import { concatenateAudioTrack } from "./src/tools/concatenate_audio_track.js";
import { computeVisualTimings } from "./src/tools/compute_visual_timings.js";
import { renderVisual } from "./src/tools/render_visual.js";
import { composeFinalVideo } from "./src/tools/compose_final_video.js";

const PROJECT_ROOT = resolve(import.meta.dirname, "../../..");
const OUTPUT_DIR = join(PROJECT_ROOT, "output");
const EPISODE_PATH = join(OUTPUT_DIR, "episode_quickbooks.json");

function log(msg: string) {
  process.stderr.write(`\n=== ${msg} ===\n`);
}

function probeDurationMs(path: string): number {
  const r = spawnSync(
    "ffprobe",
    ["-v", "error", "-show_entries", "format=duration",
     "-of", "default=noprint_wrappers=1:nokey=1", path],
    { encoding: "utf-8" }
  );
  return Math.round(parseFloat((r.stdout || "").trim()) * 1000);
}

async function main() {
  const episode = JSON.parse(readFileSync(EPISODE_PATH, "utf-8"));
  const runDir = join(OUTPUT_DIR, "build_quickbooks");
  const visualsDir = join(runDir, "visuals");
  mkdirSync(visualsDir, { recursive: true });

  // 1. validate
  log("validate_episode_json");
  const v = await validateEpisodeJson(episode);
  if (!v.valid) {
    console.error(JSON.stringify(v.errors, null, 2));
    throw new Error("episode.json failed schema validation");
  }
  process.stderr.write("valid: true\n");

  // 2. generate_audio (Kokoro TTS, per-line WAV + ffprobe durations)
  log("generate_audio");
  const audioDir = join(runDir, "clips");
  const { clips } = await generateAudio(episode, audioDir);
  process.stderr.write(`synthesized ${clips.length} clips\n`);

  // 3. concatenate_audio_track (writes clip_durations.json next to out)
  log("concatenate_audio_track");
  const audioPath = join(runDir, "episode.wav");
  const concat = await concatenateAudioTrack(clips as any, audioPath);
  process.stderr.write(`audio: ${concat.out_path} (${concat.clip_count} clips)\n`);
  const actualAudioMs = probeDurationMs(audioPath);
  process.stderr.write(`probed audio duration: ${actualAudioMs} ms\n`);

  // 4. compute_visual_timings (segment-level; reads clip_durations.json)
  log("compute_visual_timings");
  const timings = await computeVisualTimings(episode, clips as any, runDir);
  process.stderr.write(
    `segments: ${timings.timings.length}, total_duration_ms: ${timings.total_duration_ms}\n`
  );

  // Build an absolute per-line start map from the probed durations, so anchor
  // visuals land exactly where their start_with_line begins on the master
  // timeline. Mirrors compute_visual_timings' accumulation but at line grain.
  const probed = new Map<string, number>();
  const cdPath = join(runDir, "clip_durations.json");
  if (existsSync(cdPath)) {
    const raw = JSON.parse(readFileSync(cdPath, "utf-8"));
    for (const c of raw.clips ?? []) probed.set(c.line_id, c.duration_ms);
  }
  const durByLine = new Map<string, number>();
  for (const c of clips as any[]) durByLine.set(c.line_id, probed.get(c.line_id) ?? c.duration_ms);

  const lineStart = new Map<string, number>();
  let cursor = 0;
  for (const seg of episode.segments) {
    for (const line of seg.lines) {
      lineStart.set(line.line_id, cursor);
      cursor += durByLine.get(line.line_id) ?? 0;
      cursor += line.pause_after_ms ?? 250;
    }
  }

  // 5. render_visual for the animated base background + each anchor visual.
  log("render_visual (base background)");
  const basePath = join(visualsDir, "base_bg.mp4");
  await renderVisual(
    "PodcastBackground",
    { topic: episode.topic, subtitle: "PROJECT GAKU" },
    8000,
    basePath
  );
  process.stderr.write(`base: ${basePath}\n`);

  const timedVisuals: Array<Record<string, unknown>> = [];
  for (const seg of episode.segments) {
    for (const vis of seg.visuals ?? []) {
      const anchor = (vis.start_with_line ?? vis.start_after_line) as string;
      const startMs = lineStart.get(anchor) ?? 0;
      const outPath = join(visualsDir, `${vis.visual_id}.mp4`);
      log(`render_visual ${vis.template} (${vis.visual_id}) @ ${startMs}ms`);
      await renderVisual(vis.template, vis.data, vis.duration_ms, outPath);
      timedVisuals.push({
        visual_id: vis.visual_id,
        template: vis.template,
        path: outPath,
        start_ms: startMs,
        end_ms: startMs + vis.duration_ms,
        duration_ms: vis.duration_ms,
      });
    }
  }
  process.stderr.write(
    `rendered ${timedVisuals.length} anchor visuals:\n` +
      timedVisuals
        .map((t) => `  ${t.visual_id} ${t.start_ms}-${t.end_ms}ms`)
        .join("\n") + "\n"
  );

  // 6. compose_final_video (overlay anchors over looped base + audio)
  log("compose_final_video");
  const finalPath = join(OUTPUT_DIR, `${episode.episode_id}.mp4`);
  const composed = await composeFinalVideo(audioPath, timedVisuals, finalPath, {
    base_video_path: basePath,
    width: 1920,
    height: 1080,
    fps: 30,
    total_duration_ms: actualAudioMs,
  });
  process.stderr.write(`final: ${composed.out_path} (${composed.visual_count} visuals)\n`);

  const finalMs = probeDurationMs(finalPath);
  const summary = {
    final_mp4: finalPath,
    final_duration_ms: finalMs,
    final_duration_readable: `${Math.floor(finalMs / 60000)}m ${Math.round((finalMs % 60000) / 1000)}s`,
    audio_path: audioPath,
    audio_duration_ms: actualAudioMs,
    compute_visual_timings_total_ms: timings.total_duration_ms,
    episode_json: EPISODE_PATH,
    visuals: timedVisuals,
  };
  writeFileSync(join(runDir, "summary.json"), JSON.stringify(summary, null, 2));
  process.stdout.write("\nPIPELINE_SUMMARY=" + JSON.stringify(summary) + "\n");
}

main().catch((e) => {
  console.error("PIPELINE FAILED:", e);
  process.exit(1);
});
