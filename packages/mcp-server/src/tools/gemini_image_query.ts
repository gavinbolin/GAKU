// gemini_image_query
// Ask Gemini for a 2-4 word concrete-and-visual Pexels search query for a
// given podcast excerpt. Used by visual_queue.build_graphics_manifest to drop
// context-aware background images into the center area of the video.

import { GoogleGenerativeAI, type GenerativeModel, type GenerateContentResult } from "@google/generative-ai";

export interface GeminiImageQueryArgs {
  excerpt: string;
}

export interface GeminiImageQueryResult {
  query: string;
  gemini_failed?: boolean;
  warning?: string;
}

const MODEL = process.env.GEMINI_MODEL || "gemini-3.6-flash";

const API_KEYS: string[] = [
  process.env.GEMINI_API_KEY,
  process.env.GEMINI_API_KEY_2,
  process.env.GEMINI_API_KEY_3,
].filter((k): k is string => typeof k === "string" && k.length > 0);
let currentKeyIndex = 0;

function isRateLimitError(err: unknown): boolean {
  const e = err as { status?: number; message?: string } | null;
  if (!e) return false;
  if (e.status === 429 || e.status === 503) return true;
  const msg = e.message || String(err);
  return msg.includes("429") || msg.includes("503") || msg.includes("high demand") || msg.includes("quota") || msg.includes("Too Many Requests");
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
      currentKeyIndex = (currentKeyIndex + 1) % API_KEYS.length;
      rotations++;
      if (rotations >= API_KEYS.length) {
        throw new Error(`All ${API_KEYS.length} Gemini API keys exhausted (429)`);
      }
    }
  }
  throw new Error("Gemini key rotation loop exited unexpectedly");
}

function cleanQuery(raw: string): string {
  // Gemini sometimes wraps the answer in quotes, prefixes "Query:", or appends
  // a trailing period. Pull out the first non-empty line and strip noise.
  const firstLine = raw
    .split(/\r?\n/)
    .map((l) => l.trim())
    .find((l) => l.length > 0) || "";
  return firstLine
    .replace(/^["'`]+|["'`.!?]+$/g, "")
    .replace(/^(query|search|answer)\s*:\s*/i, "")
    .trim();
}

export async function geminiImageQuery(
  args: GeminiImageQueryArgs
): Promise<GeminiImageQueryResult> {
  const excerpt = (args.excerpt || "").trim();
  if (!excerpt) {
    return { query: "", gemini_failed: true, warning: "Empty excerpt" };
  }
  if (API_KEYS.length === 0) {
    return { query: "", gemini_failed: true, warning: "GEMINI_API_KEY not set" };
  }

  const systemPrompt =
    "You are helping illustrate a podcast. Given a short excerpt, " +
    "you reply with ONLY a single Pexels image search query — 2 to 4 " +
    "concrete, visual, article-grounded nouns (people, places, artifacts). " +
    "Never reply with vague queries like AI, AI for, technology, future, " +
    "or single words empty/team/office/project/data/business alone. " +
    "No punctuation, no quotes, no explanation.";

  const userMessage =
    `Here is a 25-second excerpt:\n"${excerpt}"\n\n` +
    "Give me a single Pexels image search query (2-4 words, concrete and " +
    "visual) that would make a great background illustration for this moment. " +
    "Reply with ONLY the search query, nothing else.";

  try {
    const buildModel = (client: GoogleGenerativeAI) =>
      client.getGenerativeModel({
        model: MODEL,
        systemInstruction: systemPrompt,
        generationConfig: { maxOutputTokens: 32, temperature: 0.6 },
      });
    const result = await callGeminiWithRotation(buildModel, userMessage);
    const query = cleanQuery(result.response.text());
    return { query };
  } catch (err) {
    return {
      query: "",
      gemini_failed: true,
      warning: `Gemini call failed: ${(err as Error).message}`,
    };
  }
}
