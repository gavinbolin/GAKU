/** Shared with gemini_util.py; resolves correctly from src/ and dist/. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const config = JSON.parse(readFileSync(fileURLToPath(new URL("../../../config/gemini_models.json", import.meta.url)), "utf-8")) as { fallback_models: string[] };
export function geminiModels(primary?: string): string[] {
  return [...new Set([primary, process.env.GEMINI_MODEL, ...config.fallback_models]
    .map((m) => (m || "").trim()).filter(Boolean))];
}
