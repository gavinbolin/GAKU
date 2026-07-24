// compute_visual_timings
// Segment Blocks: returns per-segment absolute start/end ms.
// Reads probed durations from <run_dir>/clip_durations.json if present;
// otherwise falls back to the per-clip duration_ms in the passed `clips`
// array. The heavy lifting (manifest building, bullets, photos) now lives
// in visual_queue.py.

import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

interface ClipInput {
  line_id: string;
  duration_ms: number;
  pause_after_ms: number;
}

interface EpisodeLine {
  line_id: string;
  pause_after_ms?: number;
}

interface EpisodeSegment {
  segment_id: string;
  title?: string;
  lines: EpisodeLine[];
}

interface EpisodeShape {
  segments: EpisodeSegment[];
}

export interface SegmentTiming {
  segment_idx: number;
  segment_id: string;
  title?: string;
  start_ms: number;
  end_ms: number;
  duration_ms: number;
}

export async function computeVisualTimings(
  episodeRaw: object,
  clipsRaw: Array<Record<string, unknown>>,
  runDir?: string
): Promise<{ timings: SegmentTiming[]; total_duration_ms: number }> {
  const episode = episodeRaw as EpisodeShape;
  const clips = clipsRaw as unknown as ClipInput[];

  const probedDurations = loadProbedDurations(runDir);

  const durationByLine = new Map<string, number>();
  const pauseByLine = new Map<string, number>();
  for (const c of clips) {
    const dur = probedDurations.get(c.line_id) ?? c.duration_ms;
    durationByLine.set(c.line_id, dur);
    pauseByLine.set(c.line_id, c.pause_after_ms ?? 0);
  }
  if (probedDurations.size > 0) {
    process.stderr.write(
      `[timing] using ${probedDurations.size} probed clip durations from clip_durations.json\n`
    );
  }

  const timings: SegmentTiming[] = [];
  let cursor = 0;
  for (let i = 0; i < (episode.segments ?? []).length; i++) {
    const seg = episode.segments[i];
    const segStart = cursor;
    for (const line of seg.lines ?? []) {
      cursor += durationByLine.get(line.line_id) ?? 0;
      cursor += line.pause_after_ms ?? pauseByLine.get(line.line_id) ?? 0;
    }
    timings.push({
      segment_idx: i,
      segment_id: seg.segment_id,
      title: seg.title,
      start_ms: segStart,
      end_ms: cursor,
      duration_ms: cursor - segStart,
    });
  }

  return { timings, total_duration_ms: cursor };
}

function loadProbedDurations(runDir?: string): Map<string, number> {
  const map = new Map<string, number>();
  if (!runDir) return map;
  const path = join(runDir, "clip_durations.json");
  if (!existsSync(path)) return map;
  try {
    const raw = JSON.parse(readFileSync(path, "utf-8")) as {
      clips?: Array<{ line_id: string; duration_ms: number }>;
    };
    for (const c of raw.clips ?? []) {
      if (typeof c.line_id === "string" && typeof c.duration_ms === "number") {
        map.set(c.line_id, c.duration_ms);
      }
    }
  } catch (err) {
    process.stderr.write(
      `[timing] warning: failed to read ${path}: ${(err as Error).message}\n`
    );
  }
  return map;
}
