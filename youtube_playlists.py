"""YouTube series playlist helpers for PROJECT GAKU.

Registry: podcast-pipeline/config/playlist_series.json
  series_key -> { youtube_playlist_id?, title, description, title_tag, privacy_status }

Title pattern (documented in registry title_pattern):
  Insert " | {TAG}" before " | PROJECT GAKU" when that brand suffix is present;
  otherwise append " | {TAG}". Skip if the series tag is already present.

Thumbnail: optional series badge is documented only — not generated here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parent
REGISTRY_PATH = PIPELINE_DIR / "config" / "playlist_series.json"

# Paths that strongly indicate HBR source material (case-insensitive).
_HBR_PATH_MARKERS = (
    "hbr school",
    r"\hbr\b",
    "/hbr/",
    "\\hbr\\",
    "_hbr_",
    "hbr_source",
    "tech6750\\hbr",
    "tech6750/hbr",
    "..school\\tech6750\\hbr",
)

_BRAND_SUFFIX = " | PROJECT GAKU"


def registry_path() -> Path:
    return REGISTRY_PATH


def load_registry() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {"series": {}, "title_pattern": "", "thumbnail_note": ""}
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def save_registry(data: dict[str, Any]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def get_series_entry(series_key: str | None, registry: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not series_key:
        return None
    key = series_key.strip().lower()
    reg = registry if registry is not None else load_registry()
    entry = (reg.get("series") or {}).get(key)
    if not entry:
        return None
    return entry


def series_title_tag(series_key: str | None, registry: dict[str, Any] | None = None) -> str | None:
    entry = get_series_entry(series_key, registry)
    if not entry:
        return None
    tag = (entry.get("title_tag") or series_key or "").strip()
    return tag or None


def _title_has_series_tag(title: str, tag: str) -> bool:
    if not title or not tag:
        return False
    t = title.strip()
    tag_re = re.escape(tag)
    patterns = [
        rf"(?i)\|\s*{tag_re}\s*\|",
        rf"(?i)\|\s*{tag_re}\s*$",
        rf"(?i)\({tag_re}\)",
        rf"(?i)(?:^|[\s\-—,]){tag_re}(?:$|[\s\-—,|)])",
    ]
    return any(re.search(p, t) for p in patterns)


def format_title_with_series(title: str, series_key: str | None, registry: dict[str, Any] | None = None) -> str:
    """Apply series tag to a YouTube title without wrecking SEO / brand suffix."""
    base = (title or "").strip()
    tag = series_title_tag(series_key, registry)
    if not tag or not base:
        return base[:100]
    if _title_has_series_tag(base, tag):
        return base[:100]
    marker = f" | {tag}"
    if _BRAND_SUFFIX in base:
        # Insert before brand: "Episode | HBR | PROJECT GAKU"
        formatted = base.replace(_BRAND_SUFFIX, marker + _BRAND_SUFFIX, 1)
    else:
        formatted = base + marker
    return formatted[:100]


def detect_series(
    *,
    explicit: str | None = None,
    episode: dict[str, Any] | None = None,
    run_dir: Path | str | None = None,
    research_md: Path | str | None = None,
    source_path: Path | str | None = None,
    topic: str | None = None,
    tags: list[str] | None = None,
) -> str | None:
    """Resolve series_key. Prefer explicit metadata / CLI over heuristics.

    Order:
      1) explicit (--series)
      2) episode['series']
      3) path signals (HBR SCHOOL, _hbr_, hbr_source, folder names)
      4) topic / tags containing HBR (and not clearly a different series like langchain alone)
    """
    if explicit and str(explicit).strip():
        return str(explicit).strip().lower()

    ep = episode or {}
    if ep.get("series"):
        return str(ep["series"]).strip().lower()

    path_blobs: list[str] = []
    for p in (run_dir, research_md, source_path):
        if p:
            path_blobs.append(str(p))
    # Also scan run_dir for hbr_source.md when present
    rd = Path(run_dir) if run_dir else None
    if rd and rd.is_dir():
        path_blobs.append(rd.name)
        for child in rd.iterdir():
            if child.is_file() and (
                child.name.lower().startswith("hbr")
                or "_hbr_" in child.name.lower()
                or child.name.lower().endswith("_source.txt")
                or child.name.lower() == "hbr_source.md"
            ):
                path_blobs.append(str(child))

    joined = "\n".join(path_blobs).lower().replace("/", "\\")
    if any(m.replace("\\", "\\\\") in joined or m in joined for m in (
        "hbr school",
        "\\hbr\\",
        "_hbr_",
        "hbr_source",
        "tech6750\\hbr",
        "..school\\tech6750\\hbr",
    )):
        return "hbr"
    if re.search(r"(^|[\\_\-\s])hbr([\\_\-\s]|$)", joined):
        return "hbr"

    hay = " ".join(
        [
            str(topic or ep.get("topic") or ""),
            " ".join(tags or ep.get("keywords") or ep.get("tags") or []),
        ]
    ).lower()
    # LangChain-only without HBR should not become hbr
    if re.search(r"\bhbr\b|harvard business review", hay):
        return "hbr"

    return None


def ensure_playlist(
    youtube,
    series_key: str,
    *,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return series entry with youtube_playlist_id set; create playlist if missing."""
    key = series_key.strip().lower()
    reg = registry if registry is not None else load_registry()
    series_map = reg.setdefault("series", {})
    entry = series_map.get(key)
    if not entry:
        raise KeyError(
            f"Unknown series_key {series_key!r}. Register it in {REGISTRY_PATH} "
            "under series (title, description, title_tag)."
        )

    pid = entry.get("youtube_playlist_id")
    if pid:
        return entry

    title = entry.get("title") or key.upper()
    description = entry.get("description") or f"PROJECT GAKU series: {title}"
    privacy = (entry.get("privacy_status") or "public").strip().lower()
    if privacy not in {"private", "unlisted", "public"}:
        privacy = "public"

    body = {
        "snippet": {
            "title": title[:150],
            "description": description[:5000],
        },
        "status": {"privacyStatus": privacy},
    }
    resp = youtube.playlists().insert(part="snippet,status", body=body).execute()
    new_id = resp["id"]
    entry["youtube_playlist_id"] = new_id
    series_map[key] = entry
    save_registry(reg)
    print(f"[youtube] created playlist {title!r} -> {new_id}")
    print(f"[youtube] playlist URL: https://www.youtube.com/playlist?list={new_id}")
    return entry


def playlist_contains_video(youtube, playlist_id: str, video_id: str) -> bool:
    token = None
    while True:
        kwargs: dict[str, Any] = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        if token:
            kwargs["pageToken"] = token
        resp = youtube.playlistItems().list(**kwargs).execute()
        for item in resp.get("items", []):
            if item.get("contentDetails", {}).get("videoId") == video_id:
                return True
        token = resp.get("nextPageToken")
        if not token:
            return False


def add_video_to_series_playlist(
    youtube,
    video_id: str,
    series_key: str,
    *,
    registry: dict[str, Any] | None = None,
    skip_if_present: bool = True,
) -> dict[str, Any]:
    """Ensure playlist exists and insert video. Returns {playlist_id, video_id, added, item_id?}."""
    entry = ensure_playlist(youtube, series_key, registry=registry)
    playlist_id = entry["youtube_playlist_id"]
    if skip_if_present and playlist_contains_video(youtube, playlist_id, video_id):
        print(f"[youtube] video {video_id} already in playlist {playlist_id}")
        return {
            "playlist_id": playlist_id,
            "video_id": video_id,
            "added": False,
            "series_key": series_key.strip().lower(),
        }

    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
    }
    resp = youtube.playlistItems().insert(part="snippet", body=body).execute()
    item_id = resp.get("id")
    print(f"[youtube] added {video_id} to playlist {playlist_id} (series={series_key})")
    return {
        "playlist_id": playlist_id,
        "video_id": video_id,
        "added": True,
        "item_id": item_id,
        "series_key": series_key.strip().lower(),
        "playlist_url": f"https://www.youtube.com/playlist?list={playlist_id}",
    }


def apply_series_after_upload(
    youtube,
    *,
    video_id: str,
    title: str,
    series_key: str | None,
    update_title: bool = True,
) -> dict[str, Any]:
    """After videos.insert: format title if needed, add to series playlist.

    Returns metadata to merge into youtube.json.
    """
    out: dict[str, Any] = {}
    if not series_key:
        return out
    key = series_key.strip().lower()
    reg = load_registry()
    if key not in (reg.get("series") or {}):
        print(f"[youtube] unknown series {key!r}; skipping playlist join")
        return out

    formatted = format_title_with_series(title, key, reg)
    out["series"] = key
    out["title"] = formatted

    if update_title and formatted != title:
        try:
            meta = youtube.videos().list(part="snippet", id=video_id).execute()
            items = meta.get("items") or []
            if items:
                snippet = items[0]["snippet"]
                snippet["title"] = formatted[:100]
                youtube.videos().update(
                    part="snippet",
                    body={"id": video_id, "snippet": snippet},
                ).execute()
                print(f"[youtube] title updated with series tag -> {formatted[:100]!r}")
        except Exception as exc:
            print(f"[youtube] warning: could not update title with series tag: {exc}")

    try:
        pl = add_video_to_series_playlist(youtube, video_id, key, registry=reg)
        out["playlist_id"] = pl.get("playlist_id")
        out["playlist_url"] = pl.get("playlist_url") or (
            f"https://www.youtube.com/playlist?list={pl.get('playlist_id')}"
            if pl.get("playlist_id")
            else None
        )
        out["playlist_added"] = pl.get("added")
    except Exception as exc:
        print(f"[youtube] warning: playlist add failed: {exc}")

    return out
