# Hybrid YouTube thumbnails (PROJECT GAKU)

## Goal

Punchy one-hook thumbs that read at phone scale: cinematic dark plate + ONE big
Impact-style hook (about 2–4 words), high contrast. No small labels or corner badges
by default (series badges stay optional and off).

Style reference: audit **v2-A** ("MOST FAIL") for Most AI Initiatives Fail.

## Canonical override

Place a hand-picked PNG at:

```
output/podcasts/<episode_folder>/thumbnail.png
```

Upload helpers prefer this file and skip generation. This is the hybrid override path.

## Auto path (partial)

`podcast-pipeline/thumbnail_hybrid.py`:

1. Load plate from `visuals/visual_001_open_title.mp4` at t=0.0 (clean starfield
   before title bake-in), else `podcast.mp4` t=0, else audit frame fallbacks.
2. Darken / contrast / vignette for white type punch.
3. Draw a single centered hook line (Impact / Arial Black-style).
4. Save ≤2 MB PNG suitable for `thumbnails().set`.

Hook text resolution:

1. `episode["thumbnail_hook"]` (preferred)
2. `GAKU_THUMBNAIL_HOOK` env
3. Weak fallback: first 2–4 content words from `topic` (not punchy)

## Upload wiring

After `videos().insert`, `youtube_upload.upload_video(..., episode_dir=..., episode=...)`
calls `thumbnail_hybrid.resolve_thumbnail_path` then `thumbnails().set`.

Also used by `build_video._try_youtube_upload` and `research_podcast` upload.

Manual set for an existing video:

```powershell
$root = 'C:\Users\gavin\.PROJECT\PROJECT GAKU'
$env:PYTHONPATH = $root
Set-Location -LiteralPath $root
& "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe" -c @"
import youtube_upload as yt
from pathlib import Path
import thumbnail_hybrid as th
yt_svc = yt.get_authenticated_service()
png = Path(r'$root\output\podcasts\<ep>\thumbnail.png')
print(th.set_thumbnail_on_youtube(yt_svc, 'VIDEO_ID', png))
"@
```

## Remaining gaps (full auto hook text)

- No LLM / Gemini pass yet to invent a 2–4 word CTR hook from the episode script.
- Topic-word fallback is functional but often dull ("MOST AI INITIATIVES").
- No A/B variant generation in-pipeline (audit scripts in Temp were one-off).
- Series corner badge still documented only — intentionally not auto-applied so it
  cannot fight the one-hook design.
