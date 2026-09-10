// synthesize_research_to_script
// Hand the topic + scraped research notes to Gemini using
// prompts/gemini-script-gen.md as the system prompt; return a parsed episode
// object that conforms to schema/episode.schema.json. Pairs with the existing
// generate_audio + concatenate_audio_track tools to produce a finished podcast.
//
// Two strategies depending on requested duration:
//   - Short (< CHUNKED_THRESHOLD_MIN): single call using the full prompt, with
//     up to MAX_ATTEMPTS retries and a truncation-repair fallback.
//   - Long  (>= CHUNKED_THRESHOLD_MIN): outline call to get segment titles +
//     line targets, then one call per segment to write its dialogue. Avoids
//     the 65k output-token ceiling and produces clean JSON every time.

import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { GoogleGenerativeAI, type GenerativeModel, type GenerateContentResult } from "@google/generative-ai";

export interface SynthesizeArgs {
  topic: string;
  research_notes: string;
  target_duration_minutes?: number;
  voice_a?: string;
  voice_b?: string;
  tone?: string;
  audience_level?: string;
  focus?: string;
}

export interface SynthesizeResult {
  episode: Record<string, unknown> | null;
  raw_response: string;
  gemini_failed?: boolean;
  warning?: string;
}

const MODEL = process.env.GEMINI_MODEL || "gemini-2.5-flash";
const DEFAULT_DURATION_MIN = 8;
const DEFAULT_VOICE_A = "af_bella";
const DEFAULT_VOICE_B = "am_michael";

/** Stable display name for a Kokoro voice_id (matches visual_queue.VOICE_REGISTRY). */
const VOICE_DISPLAY_NAMES: Record<string, string> = {
  af_bella: "Bella",
  am_michael: "Michael",
  af_sarah: "Sarah",
  am_adam: "Adam",
  bf_emma: "Emma",
  bm_george: "George",
  af_nicole: "Nicole",
  am_eric: "Eric",
  bf_alice: "Alice",
  bm_daniel: "Daniel",
  bm_lewis: "Lewis",
  bf_v0isabella: "Isabella",
  bf_isabella: "Isabella",
};

function voiceDisplayName(voiceId: string): string {
  const vid = (voiceId || "").trim();
  if (VOICE_DISPLAY_NAMES[vid]) return VOICE_DISPLAY_NAMES[vid];
  const part = vid.includes("_") ? vid.split("_").slice(1).join("_") : vid;
  const cleaned = part.replace(/^v0/i, "").replace(/[_-]+/g, " ").trim();
  if (!cleaned) return "Host";
  return cleaned.replace(/\w/g, (c) => c.toUpperCase());
}
const DEFAULT_TONE = "Conversational";
const DEFAULT_AUDIENCE = "Intermediate";
const DIALOGUE_STYLE_RULES = [
  "Dialogue style (hard rules):",
  "- Never address or name the other host. Do not say Alex, Sam, Jordan, or any",
  "  host first name. No So Alex / Well Sam / Thanks Jordan / Absolutely, Alex.",
  "  Spoken text must work if the listener never learns the hosts names.",
  "- hosts[].name is metadata only and must never appear in any line text.",
  "- Ban filler: precisely (especially as a one-word reply), delve,",
  "  it's important to note, in this episode we'll dive.",
  "  Vary acknowledgements; do not lean on one tic.",
].join("\n");

// Anything at or above this requests-per-segment chunking.
const CHUNKED_THRESHOLD_MIN = 30;
const MAX_ATTEMPTS = 3;

// Gemini 2.5 Flash supports up to 65,536 output tokens. A finished episode is
// roughly (duration_minutes * 165 words * ~1.4 tokens/word) + JSON overhead.
const MAX_OUTPUT_TOKENS_CAP = 65000;
function maxOutputTokensFor(durationMin: number): number {
  const estimated = Math.ceil(durationMin * 165 * 1.4) + 4000;
  return Math.min(MAX_OUTPUT_TOKENS_CAP, Math.max(16384, estimated));
}

// ---------- API key rotation ----------
// Up to three Gemini API keys may be configured. On 429 (quota exhausted) the
// client rotates to the next key and retries immediately — a key swap is not
// counted as one of the MAX_ATTEMPTS retries.
const API_KEYS: string[] = [
  process.env.GEMINI_API_KEY,
  process.env.GEMINI_API_KEY_2,
  process.env.GEMINI_API_KEY_3,
].filter((k): k is string => typeof k === "string" && k.length > 0);
let currentKeyIndex = 0;

function isRateLimitError(err: unknown): boolean {
  const e = err as { status?: number; message?: string } | null;
  if (!e) return false;
  if (e.status === 429) return true;
  const msg = e.message || String(err);
  return msg.includes("429") || msg.includes("quota") || msg.includes("Too Many Requests");
}

async function callGeminiWithRotation(
  buildModel: (client: GoogleGenerativeAI) => GenerativeModel,
  userMessage: string
): Promise<GenerateContentResult> {
  if (API_KEYS.length === 0) {
    throw new Error("No Gemini API keys configured (GEMINI_API_KEY[_2,_3])");
  }
  let rotations = 0;
  while (rotations < API_KEYS.length) {
    const client = new GoogleGenerativeAI(API_KEYS[currentKeyIndex]);
    const model = buildModel(client);
    try {
      return await model.generateContent(userMessage);
    } catch (err) {
      if (!isRateLimitError(err)) throw err;
      const prevIdx = currentKeyIndex;
      currentKeyIndex = (currentKeyIndex + 1) % API_KEYS.length;
      rotations++;
      log(
        `429/quota on key #${prevIdx + 1}, rotating to key #${currentKeyIndex + 1} ` +
          `(rotation ${rotations}/${API_KEYS.length})`
      );
      if (rotations >= API_KEYS.length) {
        throw new Error(`All ${API_KEYS.length} Gemini API keys exhausted (429): ${(err as Error).message}`);
      }
    }
  }
  throw new Error("Gemini key rotation loop exited unexpectedly");
}

function resolvePromptPath(): string {
  // packages/mcp-server/src/tools/synthesize_research_to_script.ts ->
  // podcast-pipeline/prompts/gemini-script-gen.md
  const here = fileURLToPath(import.meta.url);
  return resolve(here, "..", "..", "..", "..", "..", "prompts", "gemini-script-gen.md");
}

// ---------- JSON extraction & repair ----------

function stripFences(text: string): string {
  const trimmed = text.trim();
  // Balanced fenced block.
  const fenced = trimmed.match(/^```(?:json|JSON)?\s*([\s\S]*?)\s*```$/);
  if (fenced) return fenced[1].trim();
  // Opening fence without a matching closer (common when Gemini gets truncated).
  if (trimmed.startsWith("```")) {
    return trimmed.replace(/^```(?:json|JSON)?\s*/, "").replace(/```\s*$/, "").trim();
  }
  return trimmed;
}

function tryParse(text: string): Record<string, unknown> | null {
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/**
 * Walks the text starting at the `[` of an array, tracking string and brace
 * state, and returns the index immediately after the last complete top-level
 * element (i.e. after a matching `}` that brought depth back to 0). Returns
 * -1 if no complete elements were found.
 */
function lastCompleteArrayElementEnd(text: string, openBracketIdx: number): number {
  let depth = 0;
  let inString = false;
  let escape = false;
  let lastComplete = -1;
  for (let i = openBracketIdx + 1; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escape) escape = false;
      else if (ch === "\\") escape = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === "{" || ch === "[") {
      depth++;
    } else if (ch === "}" || ch === "]") {
      if (depth === 0) {
        // Closing bracket of the outer array itself — stop walking.
        return lastComplete;
      }
      depth--;
      if (depth === 0) lastComplete = i + 1;
    }
  }
  return lastComplete;
}

/**
 * Last-resort repair: if Gemini got cut off mid-segment, find the position of
 * `"segments": [` and truncate the text after the last complete segment
 * object, then close the array and outer object so the result parses.
 */
function repairTruncatedEpisode(text: string): Record<string, unknown> | null {
  const segMatch = text.match(/"segments"\s*:\s*\[/);
  if (!segMatch || segMatch.index === undefined) return null;
  const openBracketIdx = segMatch.index + segMatch[0].length - 1;
  const lastEnd = lastCompleteArrayElementEnd(text, openBracketIdx);
  if (lastEnd <= openBracketIdx + 1) return null; // no complete element
  const repaired = text.slice(0, lastEnd) + "]}";
  return tryParse(repaired);
}

function extractJson(text: string): Record<string, unknown> | null {
  const stripped = stripFences(text);
  const direct = tryParse(stripped);
  if (direct) return direct;

  // Grab the outermost {...} block.
  const firstBrace = stripped.indexOf("{");
  const lastBrace = stripped.lastIndexOf("}");
  if (firstBrace >= 0 && lastBrace > firstBrace) {
    const slice = stripped.slice(firstBrace, lastBrace + 1);
    const parsed = tryParse(slice);
    if (parsed) return parsed;
  }

  // Truncation repair as a last resort.
  return repairTruncatedEpisode(stripped);
}

// ---------- Episode post-processing ----------

function patchVoicesAndDuration(
  episode: Record<string, unknown>,
  voiceA: string,
  voiceB: string,
  duration: number
): Record<string, unknown> {
  // Gemini sometimes invents voice IDs or picks defaults from the prompt
  // example. Force the user-selected voices and duration so the audio step
  // doesn't fail with an unknown voice.
  episode.target_duration_minutes = duration;
  const hosts = (episode.hosts as Array<Record<string, unknown>> | undefined) ?? [];
  if (hosts.length >= 1 && hosts[0]) {
    hosts[0].voice_id = voiceA;
    hosts[0].name = voiceDisplayName(voiceA);
  }
  if (hosts.length >= 2 && hosts[1]) {
    hosts[1].voice_id = voiceB;
    hosts[1].name = voiceDisplayName(voiceB);
  }
  episode.hosts = hosts;
  return episode;
}

function log(msg: string): void {
  // MCP server uses stdout for JSON-RPC, so logging must go to stderr.
  process.stderr.write(`[synthesize] ${msg}\n`);
}

function previewRaw(raw: string, max = 400): string {
  const trimmed = raw.replace(/\s+/g, " ").trim();
  return trimmed.length > max ? `${trimmed.slice(0, max)}…(+${trimmed.length - max} chars)` : trimmed;
}

// ---------- Single-call path with retry ----------

async function generateSingle(
  systemPrompt: string,
  userMessage: string,
  duration: number
): Promise<{ episode: Record<string, unknown> | null; raw: string; warning?: string }> {
  const buildModel = (client: GoogleGenerativeAI) =>
    client.getGenerativeModel({
      model: MODEL,
      systemInstruction: systemPrompt,
      generationConfig: {
        maxOutputTokens: maxOutputTokensFor(duration),
        responseMimeType: "application/json",
      },
    });

  let lastRaw = "";
  let lastWarning = "";
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      const result = await callGeminiWithRotation(buildModel, userMessage);
      const raw = result.response.text();
      lastRaw = raw;
      const parsed = extractJson(raw);
      if (parsed) {
        if (attempt > 1) log(`single-call succeeded on attempt ${attempt}`);
        return { episode: parsed, raw };
      }
      lastWarning = "no parseable JSON in response";
      log(
        `attempt ${attempt}/${MAX_ATTEMPTS} returned unparseable JSON ` +
          `(${raw.length} chars). Preview: ${previewRaw(raw)}`
      );
    } catch (err) {
      lastWarning = (err as Error).message;
      log(`attempt ${attempt}/${MAX_ATTEMPTS} threw: ${lastWarning}`);
    }
  }

  return {
    episode: null,
    raw: lastRaw,
    warning: `Gemini single-call failed after ${MAX_ATTEMPTS} attempts: ${lastWarning || "no JSON"}`,
  };
}

// ---------- Chunked path: outline + per-segment ----------

interface OutlineSegment {
  segment_id: string;
  title: string;
  kind: string; // "hook" | "beat" | "recap" | "close"
  intent?: string;
  target_lines?: number;
}

interface OutlineResponse {
  episode_id?: string;
  topic?: string;
  segments?: OutlineSegment[];
}

function outlinePrompt(): string {
  return [
    "You are the show-runner for PROJECT GAKU, a two-host explainer podcast.",
    "Your job in this call is ONLY to produce the OUTLINE: a JSON object that",
    "names the episode and lists each segment with its title, kind, intent,",
    "and a target line count. You will NOT write any dialogue here — a",
    "separate call will fill in each segment's lines.",
    "",
    "Return JSON only — no prose, no markdown, no code fences. Schema:",
    "{",
    '  "episode_id": "kebab-case-slug",',
    '  "topic": "Human-readable topic",',
    '  "segments": [',
    '    { "segment_id": "s01_hook",  "title": "...", "kind": "hook",  "intent": "...", "target_lines": 6 },',
    '    { "segment_id": "s02_beat1", "title": "...", "kind": "beat",  "intent": "...", "target_lines": 16 },',
    "    ...",
    '    { "segment_id": "sNN_recap", "title": "...", "kind": "recap", "intent": "...", "target_lines": 4 },',
    '    { "segment_id": "sNN_close", "title": "...", "kind": "close", "intent": "...", "target_lines": 2 }',
    "  ]",
    "}",
    "",
    "Rules:",
    "- Exactly one `hook`, one `recap`, one `close`. Everything else is `beat`.",
    "- `segment_id` is lowercase kebab with a zero-padded prefix (s01, s02, …).",
    "- Scale total dialogue to TARGET_DURATION_MINUTES * 165 words combined.",
    "  Estimate ~25 words per line, so total `target_lines` across all segments",
    "  should be roughly TARGET_DURATION_MINUTES * 165 / 25.",
    "- Beat count guidance:",
    "    5–10 min  → 3–4 beats",
    "    11–20 min → 4–6 beats",
    "    21–40 min → 6–9 beats",
    "    41–75 min → 9–14 beats",
    "    76–120 min → 14–22 beats",
    "- `intent` is a one-sentence description of what each segment achieves",
    "  (used as the prompt for the dialogue-writing call). Make it concrete.",
    "- Apply TONE, AUDIENCE_LEVEL, and FOCUS when choosing what each segment covers.",
    "- Hosts never name each other in later dialogue: do not write intents like Alex explains. Refer to Host A / Host B only.",
    "- Ban filler words (precisely, delve, it's important to note) in any sample copy.",
  ].join("\n");
}

function segmentPrompt(hostA: { name: string; voice_id: string }, hostB: { name: string; voice_id: string }): string {
  return [
    "You are writing ONE segment of dialogue for PROJECT GAKU, a two-host",
    "explainer podcast. You will be given the overall topic, the segment's",
    "title and intent, a target line count, and the customization context",
    "(tone, audience level, focus).",
    "",
    "Hosts (fixed):",
    "- Host A - the curious learner. Asks questions a smart-but-not-expert listener would ask.",
    "- Host B - the knowledgeable explainer. Confident but not smug; concrete examples before abstractions.",
    "Metadata names exist for on-screen labels only - NEVER speak them.",
    "Roughly 50/50 line split. Each line is one speaker turn.",
    DIALOGUE_STYLE_RULES,
    "",
    "Return JSON only — no prose, no markdown, no code fences. Schema:",
    "{",
    '  "lines": [',
    '    { "line_id": "<segment_id>_l01", "host_id": "A", "text": "...", "pause_after_ms": 250 },',
    '    { "line_id": "<segment_id>_l02", "host_id": "B", "text": "...", "pause_after_ms": 250 }',
    "  ],",
    '  "visuals": [',
    '    { "visual_id": "<segment_id>_v01", "template": "TitleCard|GrowthCurve|StatCallout",',
    '      "start_after_line": "<segment_id>_l01", "duration_ms": 4000, "data": { ... } }',
    "  ],",
    '  "overlays": [',
    '    { "type": "bullets", "title": "Key Points", "items": ["...", "..."], "show_at_line": 2, "hide_at_line": 6 },',
    '    { "type": "stat", "label": "Market Size", "value": "$4.8T", "subtext": "...", "show_at_line": 3, "hide_at_line": 5 },',
    '    { "type": "chart", "chart_type": "growth", "title": "...", "data_points": [{"label":"2020","value":64}], "show_at_line": 4, "hide_at_line": 8 }',
    "  ]",
    "}",
    "",
    "Rules:",
    "- Produce roughly `target_lines` total lines (±20% OK). Lines avg ~25 words.",
    "- `line_id` MUST start with the segment_id followed by `_lNN` (zero-padded).",
    "- `host_id` is exactly \"A\" or \"B\".",
    "- `pause_after_ms` is 150-400 normally, 600-1000 at a topic shift.",
    "- `visuals` is OPTIONAL. Include 0-2 per segment, only if they reinforce a key beat.",
    "  Allowed `template` values: TitleCard, GrowthCurve, StatCallout. No others.",
    "  StatCallout `data.value` MUST be a number, not a string.",
    "  `start_after_line` must reference a `line_id` you emit in this segment.",
    "- `overlays` is REQUIRED: 1-3 items per segment. Each is one of:",
    "    bullets:  { type, title, items[2-6], show_at_line, hide_at_line }",
    "    stat:     { type, label, value (string), subtext?, show_at_line, hide_at_line }",
    "    chart:    { type, chart_type:\"growth\", title, data_points[3-8] of {label,value(number)}, show_at_line, hide_at_line }",
    "  show_at_line / hide_at_line are 1-based line indices within this segment;",
    "  hide_at_line must exceed show_at_line. Overlays are visual aids that appear",
    "  ON TOP of the SegmentCard while the topic is actively being discussed.",
    "- Apply TONE, AUDIENCE_LEVEL, and FOCUS faithfully.",
    DIALOGUE_STYLE_RULES,
  ].join("\n");
}

async function generateOutline(
  userContext: string,
  duration: number
): Promise<{ outline: OutlineResponse | null; raw: string; warning?: string }> {
  const buildModel = (client: GoogleGenerativeAI) =>
    client.getGenerativeModel({
      model: MODEL,
      systemInstruction: outlinePrompt(),
      generationConfig: {
        maxOutputTokens: 8192,
        responseMimeType: "application/json",
      },
    });
  let lastRaw = "";
  let lastWarning = "";
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      const result = await callGeminiWithRotation(buildModel, userContext);
      const raw = result.response.text();
      lastRaw = raw;
      const parsed = extractJson(raw) as OutlineResponse | null;
      if (parsed && Array.isArray(parsed.segments) && parsed.segments.length > 0) {
        if (attempt > 1) log(`outline succeeded on attempt ${attempt}`);
        log(`outline ready: ${parsed.segments.length} segments`);
        return { outline: parsed, raw };
      }
      lastWarning = "outline had no segments or unparseable JSON";
      log(
        `outline attempt ${attempt}/${MAX_ATTEMPTS} unparseable. ` +
          `Preview: ${previewRaw(raw)}`
      );
    } catch (err) {
      lastWarning = (err as Error).message;
      log(`outline attempt ${attempt}/${MAX_ATTEMPTS} threw: ${lastWarning}`);
    }
  }
  log(`outline failed after ${MAX_ATTEMPTS} attempts (duration=${duration} min)`);
  return { outline: null, raw: lastRaw, warning: `Outline generation failed: ${lastWarning || "no JSON"}` };
}

function buildSegmentUserMessage(
  topic: string,
  duration: number,
  tone: string,
  audience: string,
  focus: string,
  research: string,
  seg: OutlineSegment
): string {
  return [
    `TOPIC: ${topic}`,
    `TARGET_DURATION_MINUTES: ${duration}`,
    `TONE: ${tone}`,
    `AUDIENCE_LEVEL: ${audience}`,
    `FOCUS: ${focus || "(none)"}`,
    "",
    `SEGMENT_ID: ${seg.segment_id}`,
    `SEGMENT_TITLE: ${seg.title}`,
    `SEGMENT_KIND: ${seg.kind}`,
    `SEGMENT_INTENT: ${seg.intent || "(see title)"}`,
    `TARGET_LINES: ${seg.target_lines ?? 10}`,
    "",
    "RESEARCH_NOTES:",
    research,
  ].join("\n");
}

interface SegmentBody {
  lines?: Array<Record<string, unknown>>;
  visuals?: Array<Record<string, unknown>>;
  overlays?: Array<Record<string, unknown>>;
}

async function generateSegment(
  buildModel: (client: GoogleGenerativeAI) => GenerativeModel,
  userMessage: string,
  segId: string
): Promise<SegmentBody | null> {
  let lastRaw = "";
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      const result = await callGeminiWithRotation(buildModel, userMessage);
      const raw = result.response.text();
      lastRaw = raw;
      const parsed = extractJson(raw) as SegmentBody | null;
      if (parsed && Array.isArray(parsed.lines) && parsed.lines.length > 0) {
        return parsed;
      }
      log(
        `segment ${segId} attempt ${attempt}/${MAX_ATTEMPTS} had no lines. ` +
          `Preview: ${previewRaw(raw)}`
      );
    } catch (err) {
      log(`segment ${segId} attempt ${attempt}/${MAX_ATTEMPTS} threw: ${(err as Error).message}`);
    }
  }
  log(`segment ${segId} failed after ${MAX_ATTEMPTS} attempts. Last raw: ${previewRaw(lastRaw)}`);
  return null;
}

async function generateChunked(
  args: SynthesizeArgs,
  duration: number,
  tone: string,
  audience: string,
  focus: string,
  voiceA: string,
  voiceB: string
): Promise<{ episode: Record<string, unknown> | null; raw: string; warning?: string }> {
  log(`using chunked strategy (duration=${duration} min >= ${CHUNKED_THRESHOLD_MIN})`);

  const outlineUserMessage = [
    `TOPIC: ${args.topic}`,
    `TARGET_DURATION_MINUTES: ${duration}`,
    `TONE: ${tone}`,
    `AUDIENCE_LEVEL: ${audience}`,
    `FOCUS: ${focus || "(none)"}`,
    "",
    "RESEARCH_NOTES:",
    args.research_notes,
  ].join("\n");

  const outlineResult = await generateOutline(outlineUserMessage, duration);
  if (!outlineResult.outline || !outlineResult.outline.segments) {
    return { episode: null, raw: outlineResult.raw, warning: outlineResult.warning };
  }
  const outline = outlineResult.outline;
  const segments = outline.segments!;

  const hostA = { name: voiceDisplayName(voiceA), voice_id: voiceA };
  const hostB = { name: voiceDisplayName(voiceB), voice_id: voiceB };
  const buildSegmentModel = (client: GoogleGenerativeAI) =>
    client.getGenerativeModel({
      model: MODEL,
      systemInstruction: segmentPrompt(hostA, hostB),
      generationConfig: {
        maxOutputTokens: 16384,
        responseMimeType: "application/json",
      },
    });

  const filledSegments: Array<Record<string, unknown>> = [];
  let allRaw = `OUTLINE:\n${outlineResult.raw}\n`;

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    log(`writing segment ${i + 1}/${segments.length}: ${seg.segment_id} — ${seg.title}`);
    const userMessage = buildSegmentUserMessage(
      args.topic,
      duration,
      tone,
      audience,
      focus,
      args.research_notes,
      seg
    );
    const body = await generateSegment(buildSegmentModel, userMessage, seg.segment_id);
    if (!body) {
      return {
        episode: null,
        raw: allRaw,
        warning: `Segment ${seg.segment_id} failed after ${MAX_ATTEMPTS} attempts.`,
      };
    }
    allRaw += `\nSEGMENT ${seg.segment_id}:\n${JSON.stringify(body).slice(0, 500)}…\n`;

    filledSegments.push({
      segment_id: seg.segment_id,
      title: seg.title,
      lines: body.lines || [],
      ...(body.visuals && body.visuals.length > 0 ? { visuals: body.visuals } : {}),
      ...(body.overlays && body.overlays.length > 0 ? { overlays: body.overlays } : {}),
    });
    log(`segment ${seg.segment_id} done (${body.lines?.length ?? 0} lines)`);
  }

  const episode: Record<string, unknown> = {
    episode_id: outline.episode_id || slugify(args.topic),
    topic: outline.topic || args.topic,
    target_duration_minutes: duration,
    hosts: [
      { id: "A", name: hostA.name, voice_id: voiceA },
      { id: "B", name: hostB.name, voice_id: voiceB },
    ],
    segments: filledSegments,
  };
  log(`chunked generation complete: ${filledSegments.length} segments stitched`);
  return { episode, raw: allRaw };
}

function slugify(s: string): string {
  return (
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "episode"
  );
}

// ---------- Entry point ----------

export async function synthesizeResearchToScript(
  args: SynthesizeArgs
): Promise<SynthesizeResult> {
  const duration = args.target_duration_minutes ?? DEFAULT_DURATION_MIN;
  const voiceA = args.voice_a ?? DEFAULT_VOICE_A;
  const voiceB = args.voice_b ?? DEFAULT_VOICE_B;
  const tone = (args.tone || "").trim() || DEFAULT_TONE;
  const audience = (args.audience_level || "").trim() || DEFAULT_AUDIENCE;
  const focus = (args.focus || "").trim();

  if (API_KEYS.length === 0) {
    return {
      episode: null,
      raw_response: "",
      gemini_failed: true,
      warning: "GEMINI_API_KEY not set; cannot synthesize a script.",
    };
  }
  log(`loaded ${API_KEYS.length} Gemini API key(s) for rotation`);

  let systemPrompt: string;
  try {
    systemPrompt = await readFile(resolvePromptPath(), "utf-8");
  } catch (err) {
    return {
      episode: null,
      raw_response: "",
      gemini_failed: true,
      warning: `Failed to read script-gen prompt: ${(err as Error).message}`,
    };
  }

  try {
    let episode: Record<string, unknown> | null;
    let raw: string;
    let warning: string | undefined;

    if (duration >= CHUNKED_THRESHOLD_MIN) {
      const r = await generateChunked(args, duration, tone, audience, focus, voiceA, voiceB);
      episode = r.episode;
      raw = r.raw;
      warning = r.warning;
    } else {
      const userMessage = [
        `TOPIC: ${args.topic}`,
        `TARGET_DURATION_MINUTES: ${duration}`,
        `TONE: ${tone}`,
        `AUDIENCE_LEVEL: ${audience}`,
        `FOCUS: ${focus || "(none)"}`,
        "",
        "RESEARCH_NOTES:",
        args.research_notes,
      ].join("\n");
      const r = await generateSingle(systemPrompt, userMessage, duration);
      episode = r.episode;
      raw = r.raw;
      warning = r.warning;
    }

    if (!episode) {
      log(`final failure. raw response preview: ${previewRaw(raw, 1200)}`);
      return {
        episode: null,
        raw_response: raw,
        gemini_failed: true,
        warning: warning || "Gemini returned a response that did not contain valid JSON.",
      };
    }

    const patched = patchVoicesAndDuration(episode, voiceA, voiceB, duration);
    return { episode: patched, raw_response: raw };
  } catch (err) {
    return {
      episode: null,
      raw_response: "",
      gemini_failed: true,
      warning: `Gemini call failed: ${(err as Error).message}`,
    };
  }
}
