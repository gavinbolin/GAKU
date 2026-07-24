// vibevoiceClient — drives a locally-running VibeVoice ComfyUI server the same
// way Wan's run_i2v.py drives its server: POST an API-format workflow to
// /prompt, poll /history/{id} until the job finishes, then read the produced
// audio file straight off ComfyUI's output directory (same machine).
//
// Server defaults to http://127.0.0.1:8199 (see C:\AI\VibeVoice\HOW_TO_EXPERIMENT.md).
// Only ONE GPU job at a time — VibeVoice and Wan/AssetForge cannot co-run on the
// 8 GB card, and they live in separate ComfyUI installs (hard transformers
// version conflict), so the caller is responsible for ensuring the GPU is free.

import { readFile } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_SERVER = process.env.VIBEVOICE_URL ?? "http://127.0.0.1:8199";
// ComfyUI writes outputs here; `SaveAudio` lands them under output/<prefix>_NNNNN.flac.
const DEFAULT_OUTPUT_DIR =
  process.env.VIBEVOICE_OUTPUT_DIR ??
  "C:\\AI\\VibeVoice\\ComfyUI_windows_portable\\ComfyUI\\output";

// Node ids in two_speaker_api.json.
const NODE_SPEAKER1_LOAD = "20";
const NODE_SPEAKER2_LOAD = "21";
const NODE_VIBEVOICE = "22";
const NODE_SAVE = "23";

export interface VibeVoiceGenParams {
  /** Full tagged dialogue, each line prefixed with [1]: / [2]: (…up to [4]:). */
  taggedText: string;
  /** Reference-clip filenames as they appear in ComfyUI's input (voices) dir. */
  speaker1Clip: string;
  speaker2Clip: string;
  filenamePrefix: string;
  seed?: number;
  cfgScale?: number;
  diffusionSteps?: number;
  useSampling?: boolean;
}

export interface VibeVoiceGenResult {
  /** Absolute path to the produced audio file on disk. */
  audioPath: string;
  filename: string;
  subfolder: string;
  runtimeSec: number;
  promptId: string;
}

export interface VibeVoiceClientOptions {
  server?: string;
  outputDir?: string;
  /** Overall timeout for one generation. Default 3 h (env VIBEVOICE_TIMEOUT_MS). */
  timeoutMs?: number;
  /** How often to poll /history. Default 2 s (matches run_i2v.py). */
  pollMs?: number;
  /** Called with each elapsed-seconds tick, for progress logging. */
  onProgress?: (elapsedSec: number) => void;
}

export class VibeVoiceClient {
  private readonly server: string;
  private readonly outputDir: string;
  private readonly timeoutMs: number;
  private readonly pollMs: number;
  private readonly onProgress?: (elapsedSec: number) => void;

  constructor(opts: VibeVoiceClientOptions = {}) {
    this.server = opts.server ?? DEFAULT_SERVER;
    this.outputDir = opts.outputDir ?? DEFAULT_OUTPUT_DIR;
    // This card runs ~20x realtime for multi-speaker Q4, so a 3-min-audio
    // segment ≈ 60 min. Default generously (3 h) and allow an env override;
    // a too-low timeout throws away a completed-but-slow generation.
    this.timeoutMs =
      opts.timeoutMs ??
      (process.env.VIBEVOICE_TIMEOUT_MS ? Number(process.env.VIBEVOICE_TIMEOUT_MS) : 3 * 60 * 60 * 1000);
    this.pollMs = opts.pollMs ?? 2000;
    this.onProgress = opts.onProgress;
  }

  private async loadWorkflow(): Promise<Record<string, any>> {
    const here = dirname(fileURLToPath(import.meta.url));
    // Resolve from both src/ (tsx) and dist/ (compiled): the JSON is copied next
    // to this module in src; when running compiled, look back into src.
    const candidates = [
      join(here, "two_speaker_api.json"),
      join(here, "..", "..", "src", "vibevoice", "two_speaker_api.json"),
    ];
    for (const c of candidates) {
      try {
        return JSON.parse(await readFile(c, "utf-8"));
      } catch {
        /* try next */
      }
    }
    throw new Error("vibevoiceClient: two_speaker_api.json not found");
  }

  private async api(path: string, payload?: unknown): Promise<any> {
    const res = await fetch(`${this.server}${path}`, {
      method: payload === undefined ? "GET" : "POST",
      headers: payload === undefined ? undefined : { "content-type": "application/json" },
      body: payload === undefined ? undefined : JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`VibeVoice ${path} failed (${res.status}): ${body}`);
    }
    return res.json();
  }

  async generate(params: VibeVoiceGenParams): Promise<VibeVoiceGenResult> {
    const wf = await this.loadWorkflow();

    wf[NODE_SPEAKER1_LOAD].inputs.audio = params.speaker1Clip;
    wf[NODE_SPEAKER2_LOAD].inputs.audio = params.speaker2Clip;
    const vv = wf[NODE_VIBEVOICE].inputs;
    vv.text = params.taggedText;
    if (params.seed !== undefined) vv.seed = params.seed;
    if (params.cfgScale !== undefined) vv.cfg_scale = params.cfgScale;
    if (params.diffusionSteps !== undefined) vv.diffusion_steps = params.diffusionSteps;
    if (params.useSampling !== undefined) vv.use_sampling = params.useSampling;
    wf[NODE_SAVE].inputs.filename_prefix = params.filenamePrefix;

    const t0 = Date.now();
    const queued = await this.api("/prompt", { prompt: wf, client_id: "gaku-vibevoice" });
    const promptId: string = queued.prompt_id;
    if (!promptId) throw new Error(`VibeVoice /prompt returned no prompt_id: ${JSON.stringify(queued)}`);

    let lastTick = 0;
    for (;;) {
      if (Date.now() - t0 > this.timeoutMs) {
        throw new Error(`VibeVoice generation timed out after ${(this.timeoutMs / 1000) | 0}s (prompt ${promptId})`);
      }
      await sleep(this.pollMs);
      const hist = await this.api(`/history/${promptId}`);
      const entry = hist[promptId];
      if (entry) {
        const status = entry.status ?? {};
        if (status.completed || status.status_str === "success" || status.status_str === "error") {
          if (status.status_str === "error") {
            const msgs = JSON.stringify(status.messages ?? []);
            throw new Error(`VibeVoice job failed (prompt ${promptId}): ${msgs}`);
          }
          const out = entry.outputs?.[NODE_SAVE]?.audio?.[0];
          if (!out) {
            throw new Error(
              `VibeVoice job completed but produced no audio output (prompt ${promptId}): ${JSON.stringify(entry.outputs)}`
            );
          }
          const audioPath = join(this.outputDir, out.subfolder ?? "", out.filename);
          return {
            audioPath,
            filename: out.filename,
            subfolder: out.subfolder ?? "",
            runtimeSec: (Date.now() - t0) / 1000,
            promptId,
          };
        }
      }
      const elapsed = (Date.now() - t0) / 1000;
      if (this.onProgress && elapsed - lastTick >= 15) {
        lastTick = elapsed;
        this.onProgress(elapsed);
      }
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
