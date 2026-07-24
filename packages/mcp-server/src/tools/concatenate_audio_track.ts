// concatenate_audio_track
// Glue per-line WAV clips into a single track with smooth fade in/out.
//
// Strategy:
//   0. Pre-process every clip: re-encode to canonical 24kHz mono with an
//      80 Hz highpass + 20 ms fade-in + 20 ms fade-out. This kills three
//      problems at once:
//        (a) Kokoro's known WAV header/packet warnings disappear because we
//            decode -> filter -> re-encode through ffmpeg.
//        (b) Each clip now ends at true zero amplitude, so the concat
//            boundaries don't produce audible click/pop artifacts. Without
//            this step you can hear a faint pop on every speaker transition
//            because neural TTS vocoders often leave a small non-zero tail.
//        (c) The 80 Hz highpass removes per-voice DC offset. Kokoro voices
//            have slightly different DC bias; at a narrator handoff the
//            sudden jump from one bias to another is the audible "click"
//            you hear even when fades are present.
//   1. Pre-bake one silence WAV per unique pause length (anullsrc).
//   2. Build a flat segment list interleaving processed clips with pauses.
//   3. Write that list as an ffconcat file (one `file '<path>'` per line).
//   4. Run a single ffmpeg invocation with `-f concat -safe 0 -i list.txt` so
//      the clip paths live inside the file — not the command line. This
//      avoids Windows' ~32 KB argv limit (we previously hit ENAMETOOLONG
//      at ~290 clips).
//   5. Apply afade-in at t=0 and afade-out near the end via `-af` so the
//      stitched track doesn't start/stop with a hard edge.
//
// We rely on the concat demuxer for gapless joining instead of chaining
// per-pair acrossfades; with TTS clips that share format (sample rate, channel
// count, codec), the demuxer produces clean joins and the surrounding fades
// soften the absolute start/end of the track. This is far more robust than
// crossfade for hundreds of clips.

import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { spawn } from "node:child_process";

interface ClipInput {
  line_id: string;
  path: string;
  pause_after_ms: number;
}

interface ProbedClipDuration {
  line_id: string;
  duration_ms: number;
}

const FADE_IN_MS = 50;
const FADE_OUT_MS = 100;
const SAMPLE_RATE = 24000;

// Per-clip edge smoothing. 20 ms is long enough to absorb vocoder tail
// artifacts and any residual step at the concat boundary, short enough to
// be inaudible as a "fade" in the listening experience. Previous values
// of 5/8 ms were too short to fully mask DC-offset clicks between voices.
const CLIP_FADE_IN_MS = 20;
const CLIP_FADE_OUT_MS = 20;
// Highpass cutoff (Hz). Strips DC and sub-audible rumble. Speech energy
// starts around 85 Hz (male fundamental), so 80 Hz preserves the voice
// while killing the per-voice DC bias that causes inter-clip pops.
const HIGHPASS_HZ = 80;

export async function concatenateAudioTrack(
  clipsRaw: Array<Record<string, unknown>>,
  outPath: string
): Promise<{ out_path: string; clip_count: number; clip_durations_path?: string }> {
  const clips = clipsRaw as unknown as ClipInput[];

  if (clips.length === 0) {
    throw new Error("concatenate_audio_track: no clips provided");
  }

  const workDir = await mkdtemp(join(tmpdir(), "gaku-concat-"));

  try {
    // 0. Pre-process every clip: normalize sample rate / channel layout, apply
    //    short fade-in/out to kill TTS tail artifacts. Done sequentially to
    //    avoid swamping the box with N parallel ffmpeg processes — each clip
    //    is small so this completes in ~50-100ms apiece.
    process.stderr.write(
      `[concat] preprocessing ${clips.length} clips (normalize + edge fade)...\n`
    );
    const processedClipPaths: string[] = [];
    const probedDurations: ProbedClipDuration[] = [];
    for (let i = 0; i < clips.length; i++) {
      const srcPath = clips[i].path;
      const processedPath = join(workDir, `clip_${i.toString().padStart(4, "0")}.wav`);
      const dur = await probeDuration(srcPath);
      const fadeOutStart = Math.max(0, dur - CLIP_FADE_OUT_MS / 1000).toFixed(3);
      const fadeIn = (CLIP_FADE_IN_MS / 1000).toFixed(3);
      const fadeOut = (CLIP_FADE_OUT_MS / 1000).toFixed(3);
      await runFfmpeg([
        "-y",
        "-i", srcPath,
        "-ar", String(SAMPLE_RATE),
        "-ac", "1",
        "-acodec", "pcm_s16le",
        "-af",
        `highpass=f=${HIGHPASS_HZ},afade=t=in:d=${fadeIn},afade=t=out:st=${fadeOutStart}:d=${fadeOut}`,
        processedPath,
      ]);
      processedClipPaths.push(processedPath);

      // Probe the OUTPUT to get the true rendered duration. The pre-process
      // pass can shift duration by a sample or two due to resampling +
      // codec boundary rounding; tracking the post-process value is what
      // downstream visual timing needs to stay frame-accurate.
      const processedDur = await probeDuration(processedPath);
      probedDurations.push({
        line_id: clips[i].line_id,
        duration_ms: Math.round(processedDur * 1000),
      });
    }

    // Write the probed duration manifest next to the concatenated WAV. The
    // run directory is the parent of outPath. compute_visual_timings will
    // read this back if present.
    const runDir = dirname(outPath);
    const durationsPath = join(runDir, "clip_durations.json");
    try {
      await writeFile(
        durationsPath,
        JSON.stringify({ clips: probedDurations }, null, 2),
        "utf-8"
      );
    } catch (err) {
      process.stderr.write(
        `[concat] warning: failed to write ${durationsPath}: ${(err as Error).message}\n`
      );
    }

    // 1. Pre-bake silence WAVs for each unique non-zero pause length.
    //    Format MUST match the preprocessed clips (24kHz mono) so the
    //    concat demuxer accepts the splice without re-encoding noise.
    const uniquePauses = new Set<number>();
    for (let i = 0; i < clips.length - 1; i++) {
      if (clips[i].pause_after_ms > 0) uniquePauses.add(clips[i].pause_after_ms);
    }

    const silenceFor = new Map<number, string>();
    for (const ms of uniquePauses) {
      const silencePath = join(workDir, `silence_${ms}ms.wav`);
      const seconds = (ms / 1000).toFixed(3);
      await runFfmpeg([
        "-y",
        "-f", "lavfi",
        "-i", `anullsrc=r=${SAMPLE_RATE}:cl=mono`,
        "-af", `atrim=duration=${seconds}`,
        "-acodec", "pcm_s16le",
        silencePath,
      ]);
      silenceFor.set(ms, silencePath);
    }

    // 2. Build flat segment list using PROCESSED clip paths.
    const segments: string[] = [];
    for (let i = 0; i < clips.length; i++) {
      segments.push(processedClipPaths[i]);
      const isLast = i === clips.length - 1;
      if (!isLast && clips[i].pause_after_ms > 0) {
        segments.push(silenceFor.get(clips[i].pause_after_ms)!);
      }
    }

    const fadeIn = (FADE_IN_MS / 1000).toFixed(3);
    const fadeOut = (FADE_OUT_MS / 1000).toFixed(3);

    // Single-clip fast path: nothing to concat, just fade in/out.
    if (segments.length === 1) {
      const duration = await probeDuration(segments[0]);
      const fadeOutStart = Math.max(0, duration - FADE_OUT_MS / 1000).toFixed(3);
      await runFfmpeg([
        "-y",
        "-i", segments[0],
        "-af", `afade=t=in:d=${fadeIn},afade=t=out:st=${fadeOutStart}:d=${fadeOut}`,
        outPath,
      ]);
      return { out_path: outPath, clip_count: clips.length, clip_durations_path: durationsPath };
    }

    // 3. Write the ffconcat list. The concat demuxer reads `file '<path>'`
    //    lines; single quotes in paths must be escaped as `'\''`. Forward
    //    slashes work on every platform ffmpeg supports, so normalize.
    const listPath = join(workDir, "list.txt");
    const listBody = segments
      .map((p) => `file '${toConcatPath(p)}'`)
      .join("\n");
    await writeFile(listPath, listBody, "utf-8");

    // 4. Probe total duration so we can place the fade-out near the end.
    const segDurations = await Promise.all(segments.map(probeDuration));
    const totalDuration = segDurations.reduce((a, b) => a + b, 0);
    const fadeOutStart = Math.max(0, totalDuration - FADE_OUT_MS / 1000).toFixed(3);

    // 5. Single ffmpeg call: concat demuxer in, fade in/out applied to the
    //    joined stream, write to outPath. Re-encoding is necessary because
    //    we're applying a filter; `-c copy` is not compatible with `-af`.
    await runFfmpeg([
      "-y",
      "-f", "concat",
      "-safe", "0",
      "-i", listPath,
      "-af", `afade=t=in:d=${fadeIn},afade=t=out:st=${fadeOutStart}:d=${fadeOut}`,
      outPath,
    ]);

    return { out_path: outPath, clip_count: clips.length, clip_durations_path: durationsPath };
  } finally {
    await rm(workDir, { recursive: true, force: true });
  }
}

function toConcatPath(p: string): string {
  // Normalize backslashes (Windows) → forward slashes; escape embedded `'`.
  return p.replace(/\\/g, "/").replace(/'/g, "'\\''");
}

function probeDuration(path: string): Promise<number> {
  return new Promise((resolveP, rejectP) => {
    const proc = spawn(
      "ffprobe",
      [
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
      ],
      { stdio: ["ignore", "pipe", "inherit"] }
    );
    let buf = "";
    proc.stdout.on("data", (d) => (buf += d.toString()));
    proc.on("error", rejectP);
    proc.on("close", (code) => {
      if (code !== 0) return rejectP(new Error(`ffprobe exited ${code}`));
      const dur = parseFloat(buf.trim());
      if (Number.isNaN(dur)) return rejectP(new Error(`ffprobe parse failed: ${buf}`));
      resolveP(dur);
    });
  });
}

function runFfmpeg(args: string[]): Promise<void> {
  // -nostdin: never block reading stdin (some corrupt-packet recovery paths
  // would otherwise prompt for input and deadlock when the parent's pipe is
  // closed). -loglevel error: silence the "Packet corrupt"/header warnings
  // that Kokoro WAV headers produce, so logs stay readable.
  const fullArgs = ["-nostdin", "-loglevel", "error", ...args];
  return new Promise((resolveP, rejectP) => {
    // MCP server's stdout is the JSON-RPC channel — must not inherit it.
    const proc = spawn("ffmpeg", fullArgs, { stdio: ["ignore", "pipe", "inherit"] });
    proc.stdout?.on("data", (chunk) => process.stderr.write(chunk));
    proc.on("error", rejectP);
    proc.on("close", (code) => {
      if (code === 0) resolveP();
      else rejectP(new Error(`ffmpeg exited ${code}`));
    });
  });
}
