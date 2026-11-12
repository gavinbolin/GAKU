# YouTube thumbnails (PROJECT GAKU, v3)

Generator: `podcast-pipeline/thumbnail_hybrid.py` (Pillow + FFmpeg, local only, no API
calls). Used by `youtube_upload.upload_video` via `resolve_thumbnail_path`, which still
prefers a hand-picked `output/podcasts/<episode>/thumbnail.png` when present.

## What was wrong (v2)

- The "hook" fell back to the first 4 non-stopwords of the full title, so subtitles and
  odd fragments ("3D MODEL ANIMATION WORKS", "MATCH YOUR AI STRATEGY" with "HOW" or
  "THE" silently dropped) ended up on the image.
- One single line, shrunk until it fit the width: long titles became tiny and thin;
  there was no wrapping and no line limit.
- Text was dead-centre, so it could sit under YouTube's bottom-right duration badge on
  some sizes, and every thumbnail used the same empty starfield.

## Headline (separate from the YouTube title)

First hit wins: `hook=` argument, `episode.thumbnail_text`, legacy `thumbnail_hook`,
env `GAKU_THUMBNAIL_TEXT` / `GAKU_THUMBNAIL_HOOK`, then a deterministic heuristic:
cut the subtitle after `:` / em dash / `|` / `(` / sentence break, drop filler words,
cut long titles at the first connector ("to", "for", "in", ...) and cap at 5 whole
words. Mark the accent word with `*asterisks*` (default: last content word):

```json
"thumbnail_text": "How 3D Animation *Works*"
```

New episodes: the Gemini outline call now also asks for `thumbnail_text` (same call,
no extra request); `cleanThumbnailText` keeps it only when it is 1-6 words.

## Fitting

`fit_text` measures each candidate with Pillow (including the stroke), tries every
word-boundary split onto 1-3 lines, and takes the largest font size that fits the
layout's safe box, preferring balanced lines. It never cuts or drops words; a single
giant word just gets smaller. All layout boxes avoid `TIMESTAMP_ZONE`
(bottom-right 260x96). Heavy Impact type, thick dark stroke, soft drop shadow,
accent colour on the key word.

## Background

Best moment among B-roll beats in the rendered segment clips that have no card,
diagram or code overlay at that time (pre-code `code_examples/baseline` clips are
preferred), scored for sharpness, colour, brightness and low white-text area, plus
a bonus when the B-roll query overlaps the topic/segment titles. The caption strip
and chapter label are cropped away. Override with `thumbnail_bg.png|jpg` in the
episode folder. Fallback: open-title starfield. 3D/animation topics (or
`"thumbnail_motif": "rig"`) also get a drawn wireframe armature accent.

## Layouts

| layout | look |
|--------|------|
| `bold` (default) | left text over a dark gradient, image + accent on the right |
| `bigword` | small kicker line + one giant accent word |
| `split` | brand panel with text, image on the right behind a diagonal accent edge |

Pick with `layout=` or `GAKU_THUMBNAIL_LAYOUT`. Output: 1280x720 PNG under 2 MB
(JPEG if the path ends in `.jpg`).

## Review samples (no upload)

```powershell
& .\.venv\Scripts\python.exe tools\render_thumbnail_samples.py --episode-dir "<run>" [--headline "How 3D Animation *Works*"]
```

Writes `<run>/reviews/thumbnails/` with each layout, 320x180 previews, the old
thumbnail for comparison, a contact sheet and `samples.json`. It never overwrites
`thumbnail.png` and never calls YouTube. Setting a thumbnail on an existing video is
a separate, explicitly approved step (`set_thumbnail_on_youtube`).
