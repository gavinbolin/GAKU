// extract_pdf_text
// Spawn `python pdf_extractor.py` to walk a PDF and emit chapter JSON, then
// read it back and return chapters + a duration estimate to the caller.

import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export interface Chapter {
  chapter_num: number;
  title: string;
  text: string;
  page_start: number;
  page_end: number;
}

export interface ExtractPdfResult {
  chapters: Chapter[];
  total_chapters: number;
  estimated_duration_minutes: number;
}

const WPM = 165;

function resolveExtractorScript(): string {
  // Tool file at: packages/mcp-server/src/tools/extract_pdf_text.ts
  // Script lives at: podcast-pipeline/pdf_extractor.py
  const here = fileURLToPath(import.meta.url);
  return resolve(here, "..", "..", "..", "..", "..", "pdf_extractor.py");
}

function pythonBin(): string {
  return process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");
}

export interface ExtractPdfOptions {
  noChapters?: boolean;
  chunkPages?: number;
}

export async function extractPdfText(
  pdfPath: string,
  opts: ExtractPdfOptions = {}
): Promise<ExtractPdfResult> {
  const workDir = await mkdtemp(join(tmpdir(), "gaku-pdf-"));
  const outPath = join(workDir, "chapters.json");
  const scriptPath = resolveExtractorScript();

  try {
    const args = [scriptPath, "--pdf", pdfPath, "--out", outPath];
    if (opts.noChapters) args.push("--no-chapters");
    if (opts.chunkPages && opts.chunkPages > 0) {
      args.push("--chunk-pages", String(opts.chunkPages));
    }
    await runPython(args);
    const raw = await readFile(outPath, "utf-8");
    const parsed = JSON.parse(raw) as { title: string; chapters: Chapter[] };

    const totalWords = parsed.chapters.reduce(
      (acc, c) => acc + c.text.trim().split(/\s+/).filter(Boolean).length,
      0
    );
    const estimated_duration_minutes = Math.round((totalWords / WPM) * 10) / 10;

    return {
      chapters: parsed.chapters,
      total_chapters: parsed.chapters.length,
      estimated_duration_minutes,
    };
  } finally {
    await rm(workDir, { recursive: true, force: true });
  }
}

function runPython(args: string[]): Promise<void> {
  return new Promise((resolveP, rejectP) => {
    const proc = spawn(pythonBin(), args, { stdio: ["ignore", "inherit", "inherit"] });
    proc.on("error", rejectP);
    proc.on("close", (code) => {
      if (code === 0) resolveP();
      else rejectP(new Error(`pdf_extractor.py exited ${code}`));
    });
  });
}
