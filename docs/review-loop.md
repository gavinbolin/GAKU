# Local preview review loop

This loop renders at most 60 seconds from an **existing** episode and manifest,
reviews it, and proposes small forward-only improvements. It never researches,
re-plans, generates a script/TTS, applies fixes, uploads, or copies to Drive.
A missing manifest is an error, not permission to fetch Pexels or call Gemini.

## Run

From `podcast-pipeline`, use the pinned project Python:

```powershell
$run = 'C:\Users\gavin\.PROJECT\PROJECT GAKU\output\podcasts\<existing-run>'
.\.venv\Scripts\python.exe tools/review_loop.py --build-dir $run --preview 300-320
```

The rendered preview command always uses `--reuse-manifest --no-upload` and
`GAKU_NO_DRIVE=1`. Output is a unique local `<run>/reviews/<timestamp>/` folder.
Use `--out-dir <new-local-folder>` to choose another location. Existing output
folders are refused, so prior reports are not overwritten.

To review an already-rendered matching 20-second preview without re-rendering:

```powershell
.\.venv\Scripts\python.exe tools/review_loop.py --build-dir $run --preview 300-320 --video "$run\takeover-preview-300-320.mp4"
```

## What it checks

- ffprobe: video/audio streams, 1920x1080 resolution, expected duration (+/- .25s).
- ffmpeg: integrated loudness, true peak, loudness range; black intervals and
  freeze events; five 640px sample frames for a human frame review.
- faster-whisper: fresh CPU/int8 ASR of the preview, approximate script WER,
  matching-caption coverage, median and 95th-percentile word-start drift.
  By default `base.en` must already be cached: **no automatic model download**.
  `--allow-model-download` explicitly permits a free model download.
- Optional vision-model hook: off by default, no .env loading. `--vision` plus
  a process-environment key explicitly enables one request with at most three
  small JPEGs and 500 output tokens. This sends frames outside the PC and may
  cost money; obtain permission and verify free-tier/budget first. Never use
  this flag in a free-only run without verified free-tier availability.

Reports: `report.md`, `report.json`, `ffprobe.json`, `transcription.json`,
`script_reference.json`, logs, sample frames and a 16kHz local review WAV.
Read the report and sampled frames before accepting a suggestion. WER at the
preview boundary is approximate (uniform word selection within boundary lines);
ASR spelling errors and partial words are not necessarily script defects.
Caption sync checks the manifest, not the actual rendered glyphs: it is not OCR.
Still cards can legitimately trigger freeze detection. A short passing window
is not a full-episode certification. `PASS_WITH_REVIEW_NOTES` is intentional.

## Improvement cycle

1. Render/review two or three short, non-overlapping windows, one at a time.
2. Confirm defects visually and against original script + audio; reject false positives.
3. Propose one small forward-only code/content fix, review its diff, test, and
   commit only its exact paths on a separate branch. Do not edit a shipped run.
4. Re-run the same windows and compare the JSON measurements and sample frames.
5. A person approves the next full generation and destination; never auto-publish.

## Optional recurring routine (not installed by this setup)

Prefer reviewing after a deliberate local code change rather than repeatedly
rendering an unchanged episode. If recurring checks are desired, choose a
weekday daytime cadence in **America/Denver** and a concrete run + deadline.
A suitable saved intent is:

> Until <deadline>, review one 20-second window from <run> on BOREN-0 using the
> project venv and local review loop. Reuse the manifest, keep Drive and all
> uploads off, use only cached local ASR, and keep vision disabled. Skip a render
> if the relevant code/manifest has not changed since the last report. Compare
> results, suggest small forward-only fixes, and never auto-apply, generate a
> new episode, or publish. Stop if the computer is offline or an approval is
> required. Report only new actionable defects to Gavin in this Grok Bot chat;
> remain silent when nothing changed.

No routine, Task Scheduler entry, background watcher, or recurring paid call
was created. Scheduling and any recipient outside this chat require separate
explicit approval.
