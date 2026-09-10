// compose_final_video
// Segment Blocks composition: every visual is full-screen. Concat them in
// order, mix the podcast audio over the top, output a single MP4.
//
// Input either:
//   - `visuals`: array of { path, start_ms, end_ms? } in playback order, OR
//   - `visual_manifest_path`: path to visual_manifest.json containing a
//     `visuals` array (Segment Blocks format).
//
// All visuals are treated as full-screen. No overlays. Each is scaled to
// the output resolution, trimmed to its exact manifest duration, then
// stitched with ffmpeg xfade so the baked Ken Burns wipe transitions
// (TRANSITION_S of intro/outro on every Remotion card) blend across each
// boundary. Audio is left as a single continuous stream — the per-clip
// trim already aligns visuals to the master audio timeline and the 0.5s
// overlap on the visual side just blends pixels.

import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";

interface VisualClip {
  visual_id?: string;
  template?: string;
  path: string;
  start_ms: number;
  end_ms?: number;
  duration_ms?: number;
}

interface ManifestEntry {
  visual_id?: string;
  template?: string;
  path?: string;
  start_ms: number;
  end_ms?: number;
}

export async function composeFinalVideo(
  audioPath: string,
  visualsRaw: Array<Record<string, unknown>>,
  outPath: string,
  opts: {
    width?: number;
    height?: number;
    fps?: number;
    total_duration_ms?: number;
    visual_manifest_path?: string;
    // Accepted for backward-compat with older callers; ignored.
    base_video_path?: string;
  } = {}
): Promise<{ out_path: string; visual_count: number }> {
  let visuals = (visualsRaw as unknown as VisualClip[]).filter((v) => v && v.path);

  if (opts.visual_manifest_path && existsSync(opts.visual_manifest_path)) {
    try {
      const raw = JSON.parse(readFileSync(opts.visual_manifest_path, "utf-8"));
      const list: ManifestEntry[] = Array.isArray(raw?.visuals)
        ? raw.visuals
        : Array.isArray(raw?.manifest)
        ? raw.manifest
        : [];
      const fromManifest: VisualClip[] = list
        .filter((m) => m && m.path)
        .map((m) => ({
          visual_id: m.visual_id,
          template: m.template,
          path: m.path as string,
          start_ms: Number(m.start_ms),
          end_ms: m.end_ms !== undefined ? Number(m.end_ms) : undefined,
        }));
      if (fromManifest.length > 0) {
        visuals = fromManifest;
      }
    } catch (err) {
      process.stderr.write(
        `[compose] warning: failed to read manifest ${opts.visual_manifest_path}: ${(err as Error).message}\n`
      );
    }
  }

  const w = opts.width ?? 1920;
  const h = opts.height ?? 1080;
  const fps = opts.fps ?? 30;

  // Overlay mode: when a looped base background is supplied, the base plays for
  // the whole runtime and each visual is composited on top ONLY during its
  // [start_ms, end_ms] window. This is the "overlay-with-enable=between"
  // filtergraph the README flagged as unfinished, plus the "static neutral
  // composition between visuals" base track. Sparse anchor visuals (a title
  // card, a couple of stat callouts) land at their computed times over an
  // animated background instead of being concatenated back-to-back.
  if (opts.base_video_path && existsSync(opts.base_video_path)) {
    return composeWithBaseOverlay(audioPath, visuals, outPath, opts.base_video_path, {
      w,
      h,
      fps,
      total_duration_ms: opts.total_duration_ms,
    });
  }

  if (visuals.length === 0) {
    // Audio-only fallback — black canvas + audio.
    await runFfmpeg([
      "-nostdin",
      "-y", "-hide_banner", "-loglevel", "error",
      "-f", "lavfi",
      "-i", `color=c=black:s=${w}x${h}:r=${fps}`,
      "-i", audioPath,
      "-map", "0:v", "-map", "1:a",
      "-c:v", "libx264", "-pix_fmt", "yuv420p",
      "-c:a", "aac", "-b:a", "128k",
      "-shortest",
      outPath,
    ]);
    return { out_path: outPath, visual_count: 0 };
  }

  // Sort by start_ms so concat order is deterministic.
  visuals.sort((a, b) => a.start_ms - b.start_ms);

  // Per-clip duration: prefer (end_ms - start_ms); fall back to duration_ms;
  // last-resort 5s.
  const clipDurationsMs = visuals.map((v) => {
    if (v.end_ms !== undefined) return Math.max(100, v.end_ms - v.start_ms);
    if (v.duration_ms !== undefined) return Math.max(100, v.duration_ms);
    return 5000;
  });

  // Build inputs: each visual + audio at the end.
  const inputArgs: string[] = ["-nostdin", "-y", "-hide_banner", "-loglevel", "error"];
  for (const v of visuals) {
    inputArgs.push("-i", v.path);
  }
  inputArgs.push("-i", audioPath);
  const audioInputIdx = visuals.length;

  // Per-visual filter: scale + pad to WxH, force CFR, trim to exact duration.
  // Non-last clips get tpad clone of TRANSITION_S so xfade overlap eats PAD
  // frames, not content. Offset = unpadded cumulative duration. This keeps
  // composed video length == sum(clip durations) and stops karaoke drift
  // that the old (cumulative - i*TRANSITION) formula introduced (~0.5s per cut).
  const TRANSITION_S = 0.5;
  const lastI = visuals.length - 1;
  const filterParts: string[] = [];
  for (let i = 0; i < visuals.length; i++) {
    const dur = (clipDurationsMs[i] / 1000).toFixed(3);
    let chain =
      `[${i}:v]scale=${w}:${h}:force_original_aspect_ratio=decrease,` +
      `pad=${w}:${h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=${fps},` +
      `trim=duration=${dur},setpts=PTS-STARTPTS`;
    if (i !== lastI) {
      chain += `,tpad=stop_mode=clone:stop_duration=${TRANSITION_S.toFixed(3)}`;
    }
    filterParts.push(`${chain}[v${i}]`);
  }

  // Stitch via padded xfade. transition=fade is a pure opacity blend; the
  // scale animation is already baked into the Remotion clips.
  let lastLabel: string;
  if (visuals.length === 1) {
    lastLabel = "v0";
  } else {
    let cumulativeS = 0;
    let prevLabel = "v0";
    for (let i = 1; i < visuals.length; i++) {
      cumulativeS += clipDurationsMs[i - 1] / 1000;
      const offsetS = cumulativeS; // unpadded; pad is what xfade consumes
      const outLabel = i === visuals.length - 1 ? "vout" : `vx${i}`;
      filterParts.push(
        `[${prevLabel}][v${i}]xfade=transition=fade:duration=${TRANSITION_S.toFixed(
          3,
        )}:offset=${offsetS.toFixed(3)}[${outLabel}]`,
      );
      prevLabel = outLabel;
    }
    lastLabel = prevLabel;
  }

  const args: string[] = [
    ...inputArgs,
    "-filter_complex", filterParts.join(";"),
    "-map", `[${lastLabel}]`,
    "-map", `${audioInputIdx}:a`,
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23",
    "-c:a", "aac", "-b:a", "128k",
    "-movflags", "+faststart",
  ];

  if (opts.total_duration_ms && opts.total_duration_ms > 0) {
    args.push("-t", (opts.total_duration_ms / 1000).toFixed(3));
  } else {
    args.push("-shortest");
  }
  args.push(outPath);

  await runFfmpeg(args);
  return { out_path: outPath, visual_count: visuals.length };
}

// Composite sparse timed visuals over a looped background, mux audio.
// - Background (input 0) is looped with -stream_loop -1 and cover-scaled to WxH.
// - Each visual (inputs 1..N) is fit-scaled + padded to WxH, then time-shifted
//   so its own frame 0 lands at start_ms on the master timeline, and gated with
//   overlay enable='between(t,start,end)' so it only shows during its window.
// - Audio (input N+1) is the master clock; output length is total_duration_ms
//   (or the audio length via -shortest if not provided).
async function composeWithBaseOverlay(
  audioPath: string,
  visuals: VisualClip[],
  outPath: string,
  basePath: string,
  opts: { w: number; h: number; fps: number; total_duration_ms?: number }
): Promise<{ out_path: string; visual_count: number }> {
  const { w, h, fps } = opts;

  const timed = [...visuals]
    .filter((v) => v && v.path && existsSync(v.path))
    .map((v) => {
      const start = Math.max(0, v.start_ms ?? 0);
      const end =
        v.end_ms !== undefined
          ? v.end_ms
          : start + (v.duration_ms !== undefined ? v.duration_ms : 5000);
      return { path: v.path, start_s: start / 1000, end_s: Math.max(start, end) / 1000 };
    })
    .sort((a, b) => a.start_s - b.start_s);

  const inputArgs: string[] = ["-nostdin", "-y", "-hide_banner", "-loglevel", "error"];
  // Input 0: looped background.
  inputArgs.push("-stream_loop", "-1", "-i", basePath);
  for (const t of timed) inputArgs.push("-i", t.path);
  inputArgs.push("-i", audioPath);
  const audioInputIdx = timed.length + 1;

  const filterParts: string[] = [];
  // Cover-scale the base to fill WxH.
  filterParts.push(
    `[0:v]scale=${w}:${h}:force_original_aspect_ratio=increase,` +
      `crop=${w}:${h},setsar=1,fps=${fps}[bg]`
  );

  let prevLabel = "bg";
  for (let i = 0; i < timed.length; i++) {
    const t = timed[i];
    // Fit-scale + pad each visual, then shift its PTS so frame 0 = start_s.
    filterParts.push(
      `[${i + 1}:v]scale=${w}:${h}:force_original_aspect_ratio=decrease,` +
        `pad=${w}:${h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=${fps},` +
        `setpts=PTS-STARTPTS+${t.start_s.toFixed(3)}/TB[v${i}]`
    );
    const outLabel = `o${i}`;
    // Single quotes protect the commas inside between() from ffmpeg's
    // option-splitting. eof_action=pass keeps the base flowing once the
    // (short) overlay input has ended.
    filterParts.push(
      `[${prevLabel}][v${i}]overlay=eof_action=pass:` +
        `enable='between(t,${t.start_s.toFixed(3)},${t.end_s.toFixed(3)})'[${outLabel}]`
    );
    prevLabel = outLabel;
  }

  const args: string[] = [
    ...inputArgs,
    "-filter_complex", filterParts.join(";"),
    "-map", `[${prevLabel}]`,
    "-map", `${audioInputIdx}:a`,
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23",
    "-c:a", "aac", "-b:a", "128k",
    "-movflags", "+faststart",
  ];
  if (opts.total_duration_ms && opts.total_duration_ms > 0) {
    args.push("-t", (opts.total_duration_ms / 1000).toFixed(3));
  } else {
    args.push("-shortest");
  }
  args.push(outPath);

  await runFfmpeg(args);
  return { out_path: outPath, visual_count: timed.length };
}

function runFfmpeg(args: string[]): Promise<void> {
  return new Promise((resolveP, rejectP) => {
    const proc = spawn("ffmpeg", args, { stdio: ["ignore", "pipe", "inherit"] });
    proc.stdout?.on("data", (chunk) => process.stderr.write(chunk));
    proc.on("error", rejectP);
    proc.on("close", (code) => {
      if (code === 0) resolveP();
      else rejectP(new Error(`ffmpeg compose_final_video exited ${code}`));
    });
  });
}
