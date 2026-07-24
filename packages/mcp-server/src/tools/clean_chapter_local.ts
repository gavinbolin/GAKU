// clean_chapter_local
// Hand a raw extracted chapter to a local Ollama model using
// prompts/gemini-audiobook-gen.md as the system prompt, return the cleaned text
// with audiobook pause cues. No API key required.

import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

export interface CleanChapterLocalArgs {
  chapter_text: string;
  chapter_title: string;
  chapter_num: number;
}

export interface CleanChapterLocalResult {
  cleaned_text: string;
  pause_markers: number;
  source: "ollama";
  ollama_failed?: boolean;
  warning?: string;
}

const DEFAULT_OLLAMA_URL = "http://localhost:11434";
const DEFAULT_OLLAMA_MODEL = "llama3.1";
const PAUSE_RE = /\[(PAUSE_SHORT|PAUSE_LONG|CHAPTER_START|CHAPTER_END)\]/g;

function resolvePromptPath(): string {
  const here = fileURLToPath(import.meta.url);
  return resolve(here, "..", "..", "..", "..", "..", "prompts", "gemini-audiobook-gen.md");
}

function countPauseMarkers(text: string): number {
  const matches = text.match(PAUSE_RE);
  return matches ? matches.length : 0;
}

async function streamOllama(url: string, body: object): Promise<string> {
  const res = await fetch(`${url.replace(/\/$/, "")}/api/generate`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`Ollama HTTP ${res.status}: ${await res.text()}`);
  }
  if (!res.body) {
    throw new Error("Ollama returned no response body");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let out = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      try {
        const obj = JSON.parse(line) as { response?: string; error?: string };
        if (obj.error) throw new Error(obj.error);
        if (typeof obj.response === "string") out += obj.response;
      } catch (err) {
        throw new Error(`Failed to parse Ollama stream line: ${(err as Error).message}`);
      }
    }
  }
  const tail = buffer.trim();
  if (tail) {
    try {
      const obj = JSON.parse(tail) as { response?: string };
      if (typeof obj.response === "string") out += obj.response;
    } catch {
      // ignore trailing junk
    }
  }
  return out;
}

export async function cleanChapterLocal(
  args: CleanChapterLocalArgs
): Promise<CleanChapterLocalResult> {
  const ollamaUrl = process.env.OLLAMA_URL || DEFAULT_OLLAMA_URL;
  const ollamaModel = process.env.OLLAMA_MODEL || DEFAULT_OLLAMA_MODEL;

  let systemPrompt: string;
  try {
    systemPrompt = await readFile(resolvePromptPath(), "utf-8");
  } catch (err) {
    return {
      cleaned_text: args.chapter_text,
      pause_markers: 0,
      source: "ollama",
      ollama_failed: true,
      warning: `Failed to read audiobook prompt: ${(err as Error).message}`,
    };
  }

  const userMessage = [
    `Chapter number: ${args.chapter_num}`,
    `Chapter title: ${args.chapter_title}`,
    "",
    "Raw chapter text:",
    args.chapter_text,
  ].join("\n");

  try {
    const cleaned = await streamOllama(ollamaUrl, {
      model: ollamaModel,
      system: systemPrompt,
      prompt: userMessage,
      stream: true,
    });
    return {
      cleaned_text: cleaned,
      pause_markers: countPauseMarkers(cleaned),
      source: "ollama",
    };
  } catch (err) {
    return {
      cleaned_text: args.chapter_text,
      pause_markers: 0,
      source: "ollama",
      ollama_failed: true,
      warning: `Ollama call failed: ${(err as Error).message}`,
    };
  }
}
