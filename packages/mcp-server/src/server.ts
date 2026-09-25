#!/usr/bin/env node
/**
 * PROJECT GAKU MCP server.
 *
 * Exposes the podcast pipeline as MCP tools over stdio so an orchestrator
 * (Claude Code, a custom agent, etc.) can drive the full flow:
 *   topic -> script (gemini, external) -> validate -> TTS -> visuals -> compose
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

import { validateEpisodeJson } from "./tools/validate_episode_json.js";
import { generateAudio } from "./tools/generate_audio.js";
import { concatenateAudioTrack } from "./tools/concatenate_audio_track.js";
import { computeVisualTimings } from "./tools/compute_visual_timings.js";
import { renderVisual } from "./tools/render_visual.js";
import { composeFinalVideo } from "./tools/compose_final_video.js";
import { previewVisual } from "./tools/preview_visual.js";
import { extractPdfText } from "./tools/extract_pdf_text.js";
import { cleanChapterWithGemini } from "./tools/clean_chapter_with_gemini.js";
import { cleanChapterLocal } from "./tools/clean_chapter_local.js";
import { synthesizeResearchToScript } from "./tools/synthesize_research_to_script.js";
import { geminiImageQuery } from "./tools/gemini_image_query.js";

interface ToolDef {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  handler: (args: Record<string, unknown>) => Promise<unknown>;
}

const tools: ToolDef[] = [
  {
    name: "validate_episode_json",
    description:
      "Validate an episode payload against episode.schema.json. Returns { valid, errors? }.",
    inputSchema: {
      type: "object",
      properties: { episode: { type: "object" } },
      required: ["episode"],
    },
    handler: (args) => validateEpisodeJson(args.episode as object),
  },
  {
    name: "generate_audio",
    description:
      "Synthesize per-line audio clips for an episode using Kokoro TTS. Writes WAV files into out_dir and returns clip metadata (path, duration_ms, line_id).",
    inputSchema: {
      type: "object",
      properties: {
        episode: { type: "object" },
        out_dir: { type: "string" },
      },
      required: ["episode", "out_dir"],
    },
    handler: (args) =>
      generateAudio(
        args.episode as object,
        args.out_dir as string
      ),
  },
  {
    name: "concatenate_audio_track",
    description:
      "Concatenate per-line clips (with pause_after_ms gaps) into a single episode-level WAV/MP3. Uses ffmpeg concat.",
    inputSchema: {
      type: "object",
      properties: {
        clips: { type: "array" },
        out_path: { type: "string" },
      },
      required: ["clips", "out_path"],
    },
    handler: (args) =>
      concatenateAudioTrack(
        args.clips as Array<Record<string, unknown>>,
        args.out_path as string
      ),
  },
  {
    name: "compute_visual_timings",
    description:
      "Resolve each visual's absolute start/end ms using audio clip durations and start_with_line (or deprecated start_after_line) anchors. If run_dir is provided and contains clip_durations.json, those re-probed durations override the input clip durations.",
    inputSchema: {
      type: "object",
      properties: {
        episode: { type: "object" },
        clips: { type: "array" },
        run_dir: { type: "string" },
      },
      required: ["episode", "clips"],
    },
    handler: (args) =>
      computeVisualTimings(
        args.episode as object,
        args.clips as Array<Record<string, unknown>>,
        args.run_dir as string | undefined
      ),
  },
  {
    name: "render_visual",
    description:
      "Render a single visual via Remotion CLI to an MP4 with transparent or matching background.",
    inputSchema: {
      type: "object",
      properties: {
        template: { type: "string" },
        data: { type: "object" },
        duration_ms: { type: "number" },
        out_path: { type: "string" },
      },
      required: ["template", "data", "duration_ms", "out_path"],
    },
    handler: (args) =>
      renderVisual(
        args.template as string,
        args.data as Record<string, unknown>,
        args.duration_ms as number,
        args.out_path as string
      ),
  },
  {
    name: "compose_final_video",
    description:
      "Combine audio track + an optional looped base background + zero-or-more timed visual MP4s into the final podcast/audiobook video. Visuals must include {path, start_ms, end_ms or duration_ms}. base_video_path is the looped background composition.",
    inputSchema: {
      type: "object",
      properties: {
        audio_path: { type: "string" },
        visuals: { type: "array" },
        out_path: { type: "string" },
        base_video_path: { type: "string" },
        width: { type: "number" },
        height: { type: "number" },
        fps: { type: "number" },
        total_duration_ms: { type: "number" },
        visual_manifest_path: { type: "string" },
      },
      required: ["audio_path", "out_path"],
    },
    handler: (args) =>
      composeFinalVideo(
        args.audio_path as string,
        (args.visuals as Array<Record<string, unknown>>) ?? [],
        args.out_path as string,
        {
          base_video_path: args.base_video_path as string | undefined,
          width: args.width as number | undefined,
          height: args.height as number | undefined,
          fps: args.fps as number | undefined,
          total_duration_ms: args.total_duration_ms as number | undefined,
          visual_manifest_path: args.visual_manifest_path as string | undefined,
        }
      ),
  },
  {
    name: "extract_pdf_text",
    description:
      "Extract a PDF book into structured chapter JSON via pdf_extractor.py. Returns chapters[], total_chapters, estimated_duration_minutes. Set no_chapters=true to bypass heading detection (chunk_pages controls chunk size).",
    inputSchema: {
      type: "object",
      properties: {
        pdf_path: { type: "string" },
        no_chapters: { type: "boolean" },
        chunk_pages: { type: "number" },
      },
      required: ["pdf_path"],
    },
    handler: (args) =>
      extractPdfText(args.pdf_path as string, {
        noChapters: args.no_chapters as boolean | undefined,
        chunkPages: args.chunk_pages as number | undefined,
      }),
  },
  {
    name: "clean_chapter_with_gemini",
    description:
      "Send one raw chapter to Gemini with the audiobook system prompt, returning cleaned text with [PAUSE_*] / [CHAPTER_*] markers.",
    inputSchema: {
      type: "object",
      properties: {
        chapter_text: { type: "string" },
        chapter_title: { type: "string" },
        chapter_num: { type: "number" },
      },
      required: ["chapter_text", "chapter_title", "chapter_num"],
    },
    handler: (args) =>
      cleanChapterWithGemini({
        chapter_text: args.chapter_text as string,
        chapter_title: args.chapter_title as string,
        chapter_num: args.chapter_num as number,
      }),
  },
  {
    name: "clean_chapter_local",
    description:
      "Send one raw chapter to a local Ollama model with the audiobook system prompt, returning cleaned text with [PAUSE_*] / [CHAPTER_*] markers. No API key required.",
    inputSchema: {
      type: "object",
      properties: {
        chapter_text: { type: "string" },
        chapter_title: { type: "string" },
        chapter_num: { type: "number" },
      },
      required: ["chapter_text", "chapter_title", "chapter_num"],
    },
    handler: (args) =>
      cleanChapterLocal({
        chapter_text: args.chapter_text as string,
        chapter_title: args.chapter_title as string,
        chapter_num: args.chapter_num as number,
      }),
  },
  {
    name: "synthesize_research_to_script",
    description:
      "Take a topic + scraped research notes and ask Gemini to write a two-host podcast script that validates against episode.schema.json. Returns { episode, raw_response }. Pairs with generate_audio + concatenate_audio_track to produce a finished podcast.",
    inputSchema: {
      type: "object",
      properties: {
        topic: { type: "string" },
        research_notes: { type: "string" },
        target_duration_minutes: { type: "number" },
        voice_a: { type: "string" },
        voice_b: { type: "string" },
      },
      required: ["topic", "research_notes"],
    },
    handler: (args) =>
      synthesizeResearchToScript({
        topic: args.topic as string,
        research_notes: args.research_notes as string,
        target_duration_minutes: args.target_duration_minutes as number | undefined,
        voice_a: args.voice_a as string | undefined,
        voice_b: args.voice_b as string | undefined,
      }),
  },
  {
    name: "gemini_image_query",
    description:
      "Ask Gemini for concrete 2-4 word Pexels search queries that illustrate a podcast excerpt (optional context). Returns { query, candidates, model }. Used to drop context-aware background images into the center of the video.",
    inputSchema: {
      type: "object",
      properties: { excerpt: { type: "string" }, context: { type: "string" } },
      required: ["excerpt"],
    },
    handler: (args) =>
      geminiImageQuery({
        excerpt: args.excerpt as string,
        context: typeof args.context === "string" ? (args.context as string) : undefined,
      }),
  },
  {
    name: "preview_visual",
    description:
      "Open the Remotion studio focused on a single template + props payload for fast iteration.",
    inputSchema: {
      type: "object",
      properties: {
        template: { type: "string" },
        data: { type: "object" },
      },
      required: ["template", "data"],
    },
    handler: (args) =>
      previewVisual(
        args.template as string,
        args.data as Record<string, unknown>
      ),
  },
];

async function main() {
  const server = new Server(
    { name: "project-gaku", version: "0.1.0" },
    { capabilities: { tools: {} } }
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: tools.map(({ name, description, inputSchema }) => ({
      name,
      description,
      inputSchema,
    })),
  }));

  server.setRequestHandler(CallToolRequestSchema, async (req) => {
    const tool = tools.find((t) => t.name === req.params.name);
    if (!tool) {
      throw new Error(`Unknown tool: ${req.params.name}`);
    }
    const result = await tool.handler(
      (req.params.arguments ?? {}) as Record<string, unknown>
    );
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
    };
  });

  const transport = new StdioServerTransport();
  await server.connect(transport);
  // eslint-disable-next-line no-console
  console.error("[gaku-mcp] ready");
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error("[gaku-mcp] fatal:", err);
  process.exit(1);
});
