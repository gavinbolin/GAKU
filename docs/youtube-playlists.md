# YouTube series playlists (PROJECT GAKU)

## Registry

`podcast-pipeline/config/playlist_series.json` maps `series_key` â†’ playlist metadata:

```json
{
  "series": {
    "hbr": {
      "youtube_playlist_id": null,
      "title": "Harvard Business Review â€” Gaku",
      "description": "...",
      "title_tag": "HBR",
      "privacy_status": "public"
    }
  }
}
```

- Register a new topic by adding a key (e.g. `langchain`) with `title`, `description`, `title_tag`.
- Leave `youtube_playlist_id` null; the first upload (or backfill) creates the playlist and saves the id.

## Title pattern

Keep the episode title and brand suffix. Insert the series tag before `| PROJECT GAKU` when present:

- Before: `Most AI Initiatives Fail | PROJECT GAKU`
- After:  `Most AI Initiatives Fail | HBR | PROJECT GAKU`

If there is no brand suffix, append ` | HBR`. Skip if the tag is already present (`| HBR`, `(HBR)`, or standalone).

## Detection order

1. CLI `--series hbr`
2. `episode.series` metadata
3. Path signals: `..SCHOOL/TECH6750/HBR`, `_hbr_*` research files, `hbr_source.md`, folder names containing HBR
4. Topic / tags mentioning HBR / Harvard Business Review

Prefer writing `episode.series` at research time when the source is HBR (`--source`, research-md under HBR SCHOOL, or `_hbr_` notes).

## Upload wiring

`research_podcast.py` â†’ `youtube_upload.upload_video(..., series=...)` â†’ `youtube_playlists.apply_series_after_upload` â†’ `playlistItems.insert`.

## Thumbnail

See `docs/youtube-thumbnails.md`. Default hybrid: one-hook Impact line on a dark
starfield plate. Episode-folder `thumbnail.png` overrides generation. Series corner
badges remain optional and are **not** applied automatically (they fight punchy hooks).
## Backfill

```powershell
& "C:\Users\gavin\AppData\Local\Programs\Python\Python310\python.exe" `
  "C:\Users\gavin\..PROJECT\PROJECT GAKU\podcast-pipeline\yt_playlist_backfill.py" --series hbr
```

