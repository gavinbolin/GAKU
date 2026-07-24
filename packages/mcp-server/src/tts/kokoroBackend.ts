// KokoroBackend — the original per-line Kokoro TTS path, unchanged in behavior.
// Extracted verbatim from generate_audio.ts so it can sit behind the TtsBackend
// interface. This remains the DEFAULT backend.

import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { spawn } from "node:child_process";

import { KokoroClient } from "../kokoro/kokoroClient.js";
import type { Episode, Line } from "../../../schema/types.js";
import type { AudioClip, TtsBackend } from "./TtsBackend.js";

const DEFAULT_PAUSE_MS = 250;

export class KokoroBackend implements TtsBackend {
  readonly name = "kokoro";

  async generate(episodeRaw: object, outDir: string): Promise<{ clips: AudioClip[] }> {
    const episode = episodeRaw as Episode;
    await mkdir(outDir, { recursive: true });

    const voiceByHost = new Map(
      episode.hosts.map((h) => [h.id, h.voice_id] as const)
    );

    const client = new KokoroClient();
    const clips: AudioClip[] = [];

    for (const segment of episode.segments) {
      for (const line of segment.lines) {
        const voiceId = voiceByHost.get(line.host_id);
        if (!voiceId) {
          throw new Error(
            `No voice mapped for host_id=${line.host_id} on line ${line.line_id}`
          );
        }

        const wav = await client.synthesize(line.text, voiceId);
        const path = join(outDir, `${line.line_id}.wav`);
        await writeFile(path, wav);

        // Probe the real duration. ffprobe failure → fall back to WPM estimate
        // so we never block audio synthesis on a missing/broken ffprobe.
        let duration_ms: number;
        try {
          duration_ms = await probeDurationMs(path);
        } catch {
          duration_ms = estimateDurationMs(line);
        }

        clips.push({
          line_id: line.line_id,
          host_id: line.host_id,
          path,
          duration_ms,
          pause_after_ms: line.pause_after_ms ?? DEFAULT_PAUSE_MS,
        });
      }
    }

    return { clips };
  }
}

function estimateDurationMs(line: Line): number {
  const words = line.text.trim().split(/\s+/).length;
  const wpm = 165;
  return Math.round((words / wpm) * 60_000);
}

function probeDurationMs(path: string): Promise<number> {
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
      resolveP(Math.round(dur * 1000));
    });
  });
}
