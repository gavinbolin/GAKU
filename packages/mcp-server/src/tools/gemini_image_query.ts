// gemini_image_query
// Ask Gemini for concrete, article-grounded Pexels search queries for a given
// podcast excerpt. Used by visual_queue (broll planner) to pick B-roll.
//
// 2026-09-25 fixes (daily review): the old call used maxOutputTokens=32 on a
// thinking model with no model fallback, so replies came back empty or
// truncated ("Un", "AI", "Global"). Now: model fallback list (404/400 falls
// through), 512 output tokens, optional episode context, and up to 3
// candidate queries so the Python side can validate and fall back.

import { GoogleGenerativeAI, type GenerativeModel, type GenerateContentResult } from "@google/generative-ai";

export interface GeminiImageQueryArgs {
  excerpt: string;
  /** Optional episode/beat context (company names, industry, segment title). */
  context?: string;
}

export interface GeminiImageQueryResult {
  query: string;
  candidates?: string[];
  model?: string;
  gemini_failed?: boolean;
  warning?: string;
}

import { geminiModels } from "../gemini_models.js";
import { stopOnGeminiBudgetError } from "../gemini_safety.js";

const MODEL_FALLBACKS = geminiModels(process.env.GEMINI_IMAGE_QUERY_MODEL);
const MODEL = MODEL_FALLBACKS[0];
let stickyModel: string | null = null;

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

function isModelError(err: unknown): boolean {
  const e = err as { status?: number; message?: string } | null;
  if (!e) return false;
  if (e.status === 404) return true;
  const msg = (e.message || String(err)).toLowerCase();
  return msg.includes("404") || msg.includes("not found") || msg.includes("is not supported") || msg.includes("unknown model");
}

async function callGeminiWithFallback(
  buildModel: (client: GoogleGenerativeAI, modelName: string) => GenerativeModel,
  userMessage: string
): Promise<{ result: GenerateContentResult; model: string }> {
  if (API_KEYS.length === 0) {
    throw new Error("No Gemini API keys configured (GEMINI_API_KEY[_2,_3])");
  }
  const order = stickyModel ? [stickyModel, ...MODEL_FALLBACKS.filter((m) => m !== stickyModel)] : MODEL_FALLBACKS;
  let lastErr: unknown = null;
  for (const modelName of order) {
    for (let rotations = 0; rotations < API_KEYS.length; rotations++) {
      const client = new GoogleGenerativeAI(API_KEYS[currentKeyIndex]);
      try {
        const result = await buildModel(client, modelName).generateContent(userMessage);
        stickyModel = modelName;
        return { result, model: modelName };
      } catch (err) {
        stopOnGeminiBudgetError(err);
        lastErr = err;
        if (isModelError(err)) break; // try next model
        if (!isRateLimitError(err)) throw err;
        currentKeyIndex = (currentKeyIndex + 1) % API_KEYS.length;
      }
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(`All Gemini models/keys exhausted: ${String(lastErr)}`);
}

function cleanQuery(raw: string): string {
  return raw
    .trim()
    .replace(/^[-*\d.)\s]+/, "")
    .replace(/^["'`]+|["'`.!?]+$/g, "")
    .replace(/^(query|queries|search|answer)\s*:\s*/i, "")
    .replace(/["'`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function parseCandidates(raw: string): string[] {
  const parts = raw
    .split(/\r?\n|;|,|\|/)
    .map(cleanQuery)
    .filter((q) => q.split(" ").length >= 2 && q.length <= 60);
  return parts.filter((q, i, a) => a.findIndex((x) => x.toLowerCase() === q.toLowerCase()) === i).slice(0, 3);
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
  const context = (args.context || "").trim().slice(0, 600);

  const systemPrompt =
    "You pick stock-footage search queries (Pexels) that illustrate a moment in an " +
    "educational podcast. Each query is 2 to 4 CONCRETE, filmable nouns grounded in the " +
    "excerpt: the company's industry, the physical setting, the people and objects doing " +
    "the activity (e.g. 'airline check in counter', 'bottling plant conveyor', " +
    "'call center headset agent'). Never vague abstractions (AI, technology, future, " +
    "strategy, success, innovation, global, data alone). Never text or word-art concepts. " +
    "Software, chatbots, LLMs and knowledge assistants are NEVER robots or androids: show " +
    "people using laptops/phones/chat screens in the relevant workplace instead. " +
    "Reply with exactly 3 queries, one per line, no numbering, no quotes, no explanation.";

  const userMessage =
    (context ? `Episode context: ${context}\n\n` : "") +
    `Excerpt:\n"${excerpt.slice(0, 1500)}"\n\n` +
    "Three Pexels queries, one per line:";

  try {
    const buildModel = (client: GoogleGenerativeAI, modelName: string) =>
      client.getGenerativeModel({
        model: modelName,
        systemInstruction: systemPrompt,
        // Thinking models spend output budget on reasoning; 32 tokens left
        // truncated answers like "Un". 512 is plenty for 3 short lines.
        generationConfig: { maxOutputTokens: 512, temperature: 0.5 },
      });
    const { result, model } = await callGeminiWithFallback(buildModel, userMessage);
    let text = "";
    try {
      text = result.response.text();
    } catch {
      text = "";
    }
    const candidates = parseCandidates(text);
    if (candidates.length === 0) {
      return { query: "", candidates: [], model, gemini_failed: true, warning: `Empty/invalid reply from ${model}` };
    }
    return { query: candidates[0], candidates, model };
  } catch (err) {
    return {
      query: "",
      gemini_failed: true,
      warning: `Gemini call failed: ${(err as Error).message}`,
    };
  }
}
