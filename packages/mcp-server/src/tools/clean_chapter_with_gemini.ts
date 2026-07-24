// clean_chapter_with_gemini
// Hand a raw extracted chapter to Gemini using prompts/gemini-audiobook-gen.md
// as the system prompt, return the cleaned text with audiobook pause cues.

import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { GoogleGenerativeAI, type GenerativeModel, type GenerateContentResult } from "@google/generative-ai";

export interface CleanChapterArgs {
  chapter_text: string;
  chapter_title: string;
  chapter_num: number;
  tone?: string;
  audience_level?: string;
  focus?: string;
}

export interface CleanChapterResult {
  cleaned_text: string;
  pause_markers: number;
  gemini_failed?: boolean;
  warning?: string;
}

const MODEL = process.env.GEMINI_MODEL || "gemini-2.5-flash";
const PAUSE_RE = /\[(PAUSE_SHORT|PAUSE_LONG|CHAPTER_START|CHAPTER_END)\]/g;

// ---------- API key rotation ----------
// Up to three Gemini API keys may be configured. On 429 (quota exhausted) the
// client rotates to the next key and retries immediately.
const API_KEYS: string[] = [
  process.env.GEMINI_API_KEY,
  process.env.GEMINI_API_KEY_2,
  process.env.GEMINI_API_KEY_3,
].filter((k): k is string => typeof k === "string" && k.length > 0);
let currentKeyIndex = 0;

function logStderr(msg: string): void {
  process.stderr.write(`[clean_chapter] ${msg}\n`);
}

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
      logStderr(
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
  // packages/mcp-server/src/tools/clean_chapter_with_gemini.ts ->
  // podcast-pipeline/prompts/gemini-audiobook-gen.md
  const here = fileURLToPath(import.meta.url);
  return resolve(here, "..", "..", "..", "..", "..", "prompts", "gemini-audiobook-gen.md");
}

function countPauseMarkers(text: string): number {
  const matches = text.match(PAUSE_RE);
  return matches ? matches.length : 0;
}

export async function cleanChapterWithGemini(
  args: CleanChapterArgs
): Promise<CleanChapterResult> {
  if (API_KEYS.length === 0) {
    return {
      cleaned_text: args.chapter_text,
      pause_markers: 0,
      gemini_failed: true,
      warning: "GEMINI_API_KEY not set; returned original text.",
    };
  }

  let systemPrompt: string;
  try {
    systemPrompt = await readFile(resolvePromptPath(), "utf-8");
  } catch (err) {
    return {
      cleaned_text: args.chapter_text,
      pause_markers: 0,
      gemini_failed: true,
      warning: `Failed to read audiobook prompt: ${(err as Error).message}`,
    };
  }

  try {
    const buildModel = (client: GoogleGenerativeAI) =>
      client.getGenerativeModel({
        model: MODEL,
        systemInstruction: systemPrompt,
      });

    const tone = (args.tone || "").trim() || "Educational";
    const audience = (args.audience_level || "").trim() || "Intermediate";
    const focus = (args.focus || "").trim() || "(none)";

    const userMessage = [
      `Chapter number: ${args.chapter_num}`,
      `Chapter title: ${args.chapter_title}`,
      `TONE: ${tone}`,
      `AUDIENCE_LEVEL: ${audience}`,
      `FOCUS: ${focus}`,
      "",
      "Raw chapter text:",
      args.chapter_text,
    ].join("\n");

    const result = await callGeminiWithRotation(buildModel, userMessage);
    const cleaned = result.response.text();

    return {
      cleaned_text: cleaned,
      pause_markers: countPauseMarkers(cleaned),
    };
  } catch (err) {
    return {
      cleaned_text: args.chapter_text,
      pause_markers: 0,
      gemini_failed: true,
      warning: `Gemini call failed: ${(err as Error).message}`,
    };
  }
}
