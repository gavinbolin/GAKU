"""Backfill PROJECT GAKU videos into a series playlist.

Default: ensure HBR playlist and add known HBR public uploads.
Does NOT upload or delete videos. Optionally retitles with series tag.

Usage (CWD or PYTHONPATH = PROJECT GAKU root):
  python podcast-pipeline/yt_playlist_backfill.py --series hbr
  python podcast-pipeline/yt_playlist_backfill.py --series hbr --no-retitle
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(PIPELINE))

import youtube_upload as yt  # noqa: E402
import youtube_playlists as ytpl  # noqa: E402

# Known public uploads (as of 2026-09-24). Explicit HBR set — do not include LangChain.
HBR_BACKFILL = [
    {"video_id": "c3TtYPFwrT4", "title": "How to Make Enterprise Gen AI Work | PROJECT GAKU", "why": "HBR Valentine/Politzer/Davenport"},
    {"video_id": "TjAdetyI7zU", "title": "Most AI Initiatives Fail | PROJECT GAKU", "why": "HBR Israeli/Ascarza"},
    {"video_id": "Cut8MCsGAZc", "title": "Embracing Gen AI at Work | PROJECT GAKU", "why": "HBR Wilson/Daugherty; folder + _hbr_ research"},
    {"video_id": "yqJrf9-VR-A", "title": "How People Are Really Using AI in 2026 | PROJECT GAKU", "why": "HBR / Marc Zao-Sanders; tags+desc"},
    {"video_id": "2YzFZfiIkiA", "title": "Make Sure Your AI Strategy Actually Creates Value | PROJECT GAKU", "why": "HBR.org Kim/Mauborgne/Ji"},
    {"video_id": "XuTiDIiKrjQ", "title": "The Gen AI Playbook for Organizations | PROJECT GAKU", "why": "HBR Anand/Wu"},
]

SKIP = [
    {"video_id": "HtdifjMUoN0", "title": "LangChain Explained | PROJECT GAKU", "why": "not HBR"},
]


def _retitle(youtube, video_id: str, desired_title: str) -> str:
    meta = youtube.videos().list(part="snippet", id=video_id).execute()
    items = meta.get("items") or []
    if not items:
        print(f"[backfill] missing video {video_id}")
        return desired_title
    snippet = items[0]["snippet"]
    current = snippet.get("title") or ""
    if current == desired_title:
        return current
    snippet["title"] = desired_title[:100]
    youtube.videos().update(part="snippet", body={"id": video_id, "snippet": snippet}).execute()
    print(f"[backfill] retitled {video_id}: {current!r} -> {desired_title!r}")
    return desired_title


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill series playlist membership")
    parser.add_argument("--series", default="hbr", help="series_key in playlist_series.json")
    parser.add_argument("--no-retitle", action="store_true", help="Only add to playlist; do not change titles")
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Also scan recent channel uploads and add any with HBR tags/desc (still skips LangChain)",
    )
    args = parser.parse_args()

    series_key = args.series.strip().lower()
    youtube = yt.get_authenticated_service()
    entry = ytpl.ensure_playlist(youtube, series_key)
    playlist_id = entry["youtube_playlist_id"]
    playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
    print(f"[backfill] playlist {entry.get('title')!r}")
    print(f"[backfill] id={playlist_id}")
    print(f"[backfill] url={playlist_url}")

    targets = list(HBR_BACKFILL)
    if args.scan:
        resp = youtube.search().list(
            part="snippet", forMine=True, type="video", maxResults=25, order="date"
        ).execute()
        known = {t["video_id"] for t in targets}
        skip_ids = {s["video_id"] for s in SKIP}
        for item in resp.get("items", []):
            vid = item["id"]["videoId"]
            if vid in known or vid in skip_ids:
                continue
            title = item["snippet"]["title"]
            # fetch tags/desc
            full = youtube.videos().list(part="snippet", id=vid).execute()
            sn = (full.get("items") or [{}])[0].get("snippet") or {}
            tags = sn.get("tags") or []
            desc = sn.get("description") or ""
            hay = f"{title} {' '.join(tags)} {desc}".lower()
            if "langchain" in hay and "hbr" not in hay:
                print(f"[backfill] scan skip (langchain) {vid} {title}")
                continue
            if "hbr" in hay or "harvard business review" in hay:
                targets.append({"video_id": vid, "title": title, "why": "scan: HBR signal"})
                known.add(vid)

    results = []
    for row in targets:
        vid = row["video_id"]
        title = row["title"]
        formatted = ytpl.format_title_with_series(title, series_key)
        if not args.no_retitle:
            try:
                formatted = _retitle(youtube, vid, formatted)
            except Exception as exc:
                print(f"[backfill] retitle failed {vid}: {exc}")
        try:
            pl = ytpl.add_video_to_series_playlist(youtube, vid, series_key)
            results.append({**row, "title_final": formatted, **pl})
        except Exception as exc:
            print(f"[backfill] add failed {vid}: {exc}")
            results.append({**row, "title_final": formatted, "added": False, "error": str(exc)})

    summary = {
        "series": series_key,
        "playlist_id": playlist_id,
        "playlist_url": playlist_url,
        "playlist_title": entry.get("title"),
        "skipped": SKIP,
        "results": results,
    }
    out_path = PIPELINE / "config" / f"backfill_{series_key}_result.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"[backfill] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
