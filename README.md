# PROJECT GAKU — Podcast Pipeline

An automated AI podcast video pipeline. Drop in a topic, get back a YouTube-ready
MP4 with two AI hosts in conversation, animated visuals at the right beats, and
a notebook-aesthetic look.

```
topic
  │
  ▼
┌─────────────────┐
│ web scrape      │  (gaku.py — already in repo root)
└─────────────────┘
  │ research notes
  ▼
┌─────────────────┐
│ Gemini script   │  (prompts/gemini-script-gen.md)
└─────────────────┘
  │ episode.json
  ▼
┌─────────────────┐
│ MCP server      │
│  • validate     │
│  • TTS (Kokoro) │
│  • timings      │
│  • render       │
│  • compose      │
└─────────────────┘
  │
  ├── Kokoro TTS  (HTTP, localhost:8880)  → per-line WAV
  ├── Remotion CLI (templates/*.tsx)      → visual MP4s
  └── ffmpeg                              → final MP4
  ▼
YouTube upload
```

## Repo layout

```
podcast-pipeline/
├── packages/
│   ├── mcp-server/       Node MCP server, exposes 7 pipeline tools over stdio
│   ├── remotion/         Remotion compositions (TitleCard, GrowthCurve, StatCallout)
│   └── schema/           Shared episode schema (JSON Schema + TS types)
├── prompts/              Prompts sent to Gemini (script generation)
└── examples/             Sample episode payloads to test the pipeline end-to-end
```

The existing scrape tooling (`gaku.py`, `topics.txt`, `requirements.txt`,
`results.md`) lives in the **parent folder** and feeds research notes into the
Gemini script step.

## MCP tools exposed

| Tool                       | Purpose                                                     |
| -------------------------- | ----------------------------------------------------------- |
| `validate_episode_json`    | Validate Gemini's output against the schema                 |
| `generate_audio`           | Per-line TTS via Kokoro, returns clip metadata              |
| `concatenate_audio_track`  | Glue clips + silence into one episode-level audio file      |
| `compute_visual_timings`   | Resolve every visual's absolute start/end ms                |
| `render_visual`            | Render a single Remotion composition to MP4                 |
| `compose_final_video`      | Mux audio track + visuals into the final video              |
| `preview_visual`           | Open Remotion studio for fast iteration on a template       |

## Setup

Use the pinned [Python 3.12 project environment](docs/python-environment.md);
launch the UI with `./run.ps1`. See the [local preview review loop](docs/review-loop.md)
for bounded validation without new generation or publishing.

### 1. Node packages

```bash
cd podcast-pipeline/packages/mcp-server && npm install
cd ../remotion                          && npm install
```

### 2. Kokoro TTS (local, port 8880)

The pipeline assumes a [Kokoro FastAPI server](https://github.com/remsky/Kokoro-FastAPI)
running on `http://localhost:8880`. Quickest path is the Docker image:

```bash
docker run -d --name kokoro --gpus all -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-gpu:latest
# CPU only if you must (slow). Prefer start_kokoro_and_remotion.bat which requires GPU
# unless KOKORO_ALLOW_CPU=1:
# docker run -d --name kokoro -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest
```

Sanity check: `curl http://localhost:8880/v1/audio/voices` should list voices
including `af_bella` and `am_michael` (the defaults baked into `kokoroClient.ts`).

**Voice blocklist.** `config/voice_blocklist.json` lists Kokoro voices that must never
narrate (currently `am_adam` and any `adam` variant or blend). They are removed from
the random host pools and the Gradio dropdowns, and an explicit request (CLI flag,
old `episode.json`, Gemini output, audiobook `--voice`) is swapped for a same
gender/accent voice (`am_adam` -> `am_michael`) with a `[voices] WARNING`. Enforced
in `voice_policy.py` and, for every synth call, `packages/mcp-server/src/kokoro/voicePolicy.ts`.

Override the URL with `KOKORO_URL=http://other-host:8880` if you run it elsewhere.

`research_podcast.py` stops the Docker container named `kokoro` when a build
that used Kokoro finishes, fails, or is interrupted (Ctrl+C). It never stops
any other container. The stop runs only if this process started the container,
or `docker inspect` shows the image name contains `kokoro-fastapi`. If Kokoro
is not already answering `GET /v1/audio/voices`, the build starts an existing
`kokoro` container, or creates `ghcr.io/remsky/kokoro-fastapi-gpu:latest`
(no silent CPU fallback), and waits for that health check.

Leave the container running with `--no-kokoro-autostop` or
`GAKU_KOKORO_AUTOSTOP=0` (`false`, `no`, and `off` also disable it). The
default is on. Runs that never use Kokoro (a non-Kokoro `TTS_BACKEND`, or a
path that returns before synthesis) do not stop the container.

One-line smoke test through the same ensure / stop path:

```bash
python kokoro_lifecycle.py --smoke "Hello from Gaku."
```

That writes `output/kokoro_smoke/smoke.wav` and then stops the container.

### 3. ffmpeg

Make sure `ffmpeg` (and `ffprobe`) are on your `PATH`. On Windows:

```powershell
winget install Gyan.FFmpeg
```

### 4. Wire the MCP server into your client

Add to your MCP client config (e.g. Claude Desktop's `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "project-gaku": {
      "command": "node",
      "args": ["C:\\Users\\gavin\\.PROJECT\\PROJECT GAKU\\podcast-pipeline\\packages\\mcp-server\\dist\\server.js"]
    }
  }
}
```

(Run `npm run build` in `packages/mcp-server` first, or use `tsx` via `npm run dev`.)

## Pipeline usage

End-to-end from an orchestrator (Claude Code, custom agent, or a manual driver
script):

1. **Scrape** — run `gaku.py` (parent folder) on the topic to produce research notes.
2. **Generate script** — call Gemini with `prompts/gemini-script-gen.md` plus the
   topic, target duration, and research notes. Capture the JSON response.
3. **`validate_episode_json`** — bail out and retry the script step if invalid.
4. **`generate_audio`** — synthesizes per-line WAVs into a working dir.
5. **`concatenate_audio_track`** — produces the episode audio.
6. **`compute_visual_timings`** — turns relative anchors into absolute ms.
7. For each timing: **`render_visual`** — produces an MP4 per visual.
8. **`compose_final_video`** — final episode MP4.
9. Upload the MP4 to YouTube (out of scope of this pipeline).

`examples/episode-sample.json` contains a complete episode payload you can drop
straight into `validate_episode_json` and `generate_audio` without running the
script step.

## TODO before first real run

These are the wired-up-but-not-finished pieces. Ordered by what will bite first.

- [ ] **Audio durations.** `generate_audio` currently estimates duration with a
      WPM heuristic. Replace with a real `ffprobe` probe so visual timings line
      up with the actual audio.
- [ ] **Silence between lines.** `concatenate_audio_track` doesn't yet emit the
      silence WAVs that go between clips. Pre-bake one per unique pause length,
      or switch to an `aevalsrc` filtergraph.
- [ ] **Final compose filtergraph.** `compose_final_video` currently concats
      visuals back-to-back. Build the real overlay-with-`enable=between(t,…)`
      filtergraph so each visual lands at its computed `start_ms`.
- [ ] **Background between visuals.** Decide whether non-visual audio plays over
      a static "neutral" composition (waveform? color field?) and add it as a
      base track in the compose step.
- [ ] **Voice IDs.** Confirm `af_bella` / `am_michael` exist on your Kokoro install
      (`/v1/audio/voices`). Update `kokoroClient.ts` constants if you want
      different defaults.
- [ ] **Schema path at runtime.** `validate_episode_json` resolves the schema by
      relative path from `dist/`. Either copy the schema into `dist/` at build
      time, or switch to a bundled import.
- [ ] **MCP SDK pin.** `@modelcontextprotocol/sdk` versioning shifts often;
      pin to whatever version your MCP client speaks.
- [ ] **Gemini caller.** This repo doesn't include the Gemini call itself — wire
      it up in your orchestrator using `prompts/gemini-script-gen.md` as the
      system prompt and the schema as the response constraint.
```

## Approved Gaku improvements (2026-09)

Daily-loop items folded into prompts + `visual_queue.py` + Remotion overlays + `factcheck_episode.py` (HBR / framework listens). See `prompts/gemini-script-gen.md` section `APPROVED_GAKU_IMPROVEMENTS_2026_09`.

## Local-only safety

`research_podcast.py --no-upload` disables both YouTube upload and Drive copy.
Use `--no-drive` or `GAKU_NO_DRIVE=1` to independently disable Drive copy.
A missing Drive directory is warned about and skipped; it is never created.
`build_video.py --no-upload` remuxes/renders locally and has no Drive-copy path.

For quota/billing-sensitive attempts, set `GAKU_GEMINI_STOP_ON_QUOTA=1`.
Python helpers and podcast MCP synthesis/image-query tools then terminate on
the first quota, billing, payment, or auth signal without retry/key rotation.
An optional `GAKU_GEMINI_STOP_FILE` records a safe, credential-free stop marker.
This is a stop policy, **not** proof that a successful request is free-tier.
