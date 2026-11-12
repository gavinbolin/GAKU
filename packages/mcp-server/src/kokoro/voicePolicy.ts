// Central Kokoro voice blocklist, shared with voice_policy.py via
// podcast-pipeline/config/voice_blocklist.json. Applied to every Kokoro synth
// request (KokoroClient.synthesize) and to the hosts the script generator writes,
// so a blocked voice (e.g. am_adam) can never narrate, even if an old episode.json,
// CLI flag or Gemini output asks for it.

import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

interface VoicePolicy {
  blocked: string[];
  blocked_name_substrings: string[];
  substitutes: Record<string, string>;
  fallback_by_prefix: Record<string, string>;
  fallback: string;
}

const DEFAULT_POLICY: VoicePolicy = {
  blocked: ["am_adam"],
  blocked_name_substrings: ["adam"],
  substitutes: { am_adam: "am_michael" },
  fallback_by_prefix: { am: "am_michael", bm: "bm_george", af: "af_bella", bf: "bf_emma" },
  fallback: "am_michael",
};

let cache: VoicePolicy | null = null;
const warned = new Set<string>();

function configCandidates(): string[] {
  const here = dirname(fileURLToPath(import.meta.url));
  const fromEnv = process.env.GAKU_VOICE_BLOCKLIST;
  return [
    ...(fromEnv ? [fromEnv] : []),
    join(here, "..", "..", "..", "..", "config", "voice_blocklist.json"),
    join(process.cwd(), "config", "voice_blocklist.json"),
  ];
}

export function voicePolicy(): VoicePolicy {
  if (cache) return cache;
  let loaded: Partial<VoicePolicy> = {};
  for (const p of configCandidates()) {
    try {
      if (existsSync(p)) {
        loaded = (JSON.parse(readFileSync(p, "utf-8")).kokoro ?? {}) as Partial<VoicePolicy>;
        break;
      }
    } catch {
      /* fall back to defaults */
    }
  }
  cache = {
    ...DEFAULT_POLICY,
    ...loaded,
    blocked: [...new Set([...DEFAULT_POLICY.blocked, ...(loaded.blocked ?? [])])],
    blocked_name_substrings: [
      ...new Set([...DEFAULT_POLICY.blocked_name_substrings, ...(loaded.blocked_name_substrings ?? [])]),
    ],
  };
  return cache;
}

function baseOf(component: string): string {
  return component.trim().replace(/\([^)]*\)\s*$/, "").trim().toLowerCase();
}

function componentBlocked(component: string): boolean {
  const base = baseOf(component);
  if (!base) return false;
  const p = voicePolicy();
  if (p.blocked.some((b) => b.toLowerCase() === base)) return true;
  const name = base.includes("_") ? base.slice(base.indexOf("_") + 1) : base;
  return p.blocked_name_substrings.some((s) => name.includes(s.toLowerCase()));
}

export function isBlockedVoice(voice: string | undefined | null): boolean {
  if (!voice) return false;
  return voice.split("+").some(componentBlocked);
}

function substituteFor(component: string): string {
  const p = voicePolicy();
  const base = baseOf(component);
  const direct = p.substitutes[base];
  if (direct && !isBlockedVoice(direct)) return direct;
  const prefix = base.includes("_") ? base.slice(0, base.indexOf("_")) : "";
  const byPrefix = p.fallback_by_prefix[prefix];
  if (byPrefix && !isBlockedVoice(byPrefix)) return byPrefix;
  return p.fallback || "am_michael";
}

/** Return a non-blocked voice id, swapping blocked blend parts, with a one-time warning. */
export function enforceVoice(voice: string, context = ""): string {
  if (!isBlockedVoice(voice)) return voice;
  const out: string[] = [];
  for (const comp of voice.split("+")) {
    let c = comp.trim();
    if (componentBlocked(c)) {
      const weight = c.match(/\([^)]*\)\s*$/)?.[0] ?? "";
      c = substituteFor(c) + weight;
    }
    if (!out.some((o) => baseOf(o) === baseOf(c))) out.push(c);
  }
  const fixed = out.join("+");
  const key = `${voice}->${fixed}`;
  if (!warned.has(key)) {
    warned.add(key);
    // stderr only: stdout is the MCP JSON-RPC channel.
    console.error(
      `[voices] WARNING: Kokoro voice '${voice}' is blocklisted${context ? ` (${context})` : ""}; using '${fixed}' instead.`
    );
  }
  return fixed;
}

/** Drop blocked voices from a list (e.g. /v1/audio/voices for pickers). */
export function filterVoices(voices: string[]): string[] {
  return voices.filter((v) => !isBlockedVoice(v));
}
