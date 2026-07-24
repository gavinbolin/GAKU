// HTTP client for a locally-running Kokoro TTS server.
// Default Kokoro FastAPI port is 8880 (see kokoro-fastapi project).
//
// Usage:
//   const client = new KokoroClient();
//   const wav = await client.synthesize("Hello world", VOICE_HOST_A);

const DEFAULT_BASE_URL = process.env.KOKORO_URL ?? "http://localhost:8880";

// Two host voices used across episodes. Override per-episode in episode.hosts[].voice_id.
// These values match common kokoro-fastapi voice ids; double-check against
// the /v1/audio/voices endpoint on your local install.
export const VOICE_HOST_A = "af_bella";   // host A: curious learner
export const VOICE_HOST_B = "am_michael"; // host B: knowledgeable explainer

export interface KokoroOptions {
  baseUrl?: string;
  model?: string;
  format?: "wav" | "mp3" | "pcm";
  speed?: number;
}

export class KokoroClient {
  private readonly baseUrl: string;
  private readonly model: string;
  private readonly format: "wav" | "mp3" | "pcm";
  private readonly speed: number;

  constructor(opts: KokoroOptions = {}) {
    this.baseUrl = opts.baseUrl ?? DEFAULT_BASE_URL;
    this.model = opts.model ?? "kokoro";
    this.format = opts.format ?? "wav";
    this.speed = opts.speed ?? 1.0;
  }

  async synthesize(text: string, voiceId: string): Promise<Buffer> {
    const url = `${this.baseUrl}/v1/audio/speech`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: this.model,
        voice: voiceId,
        input: text,
        response_format: this.format,
        speed: this.speed,
      }),
    });

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(
        `Kokoro TTS failed (${res.status} ${res.statusText}): ${body}`
      );
    }

    const arrayBuf = await res.arrayBuffer();
    return Buffer.from(arrayBuf);
  }

  async listVoices(): Promise<string[]> {
    const res = await fetch(`${this.baseUrl}/v1/audio/voices`);
    if (!res.ok) throw new Error(`Kokoro listVoices failed: ${res.status}`);
    const json = (await res.json()) as { voices?: string[] };
    return json.voices ?? [];
  }
}
