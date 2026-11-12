"""Episode sources: capture, load, and the YouTube "Sources" description section.

Standing rule: every GAKU upload lists where the episode's information came from.
Sources are only ever *captured* from real research artifacts, never invented:

* ``episode["sources"]`` / ``<run>/sources.json`` (written by research_podcast after
  the research stage),
* ``<run>/results.md`` - the gaku.py scrape that was fed to Gemini as RESEARCH_NOTES
  (markdown links ``[title](url)``),
* ``<run>/hbr_source.md`` / ``_source_pdfs/*.md`` - a source article/PDF (title, plus a
  URL only if one appears in the document).

Web results whose title/snippet share no content words with the episode (topic,
keywords, segment titles) are kept in sources.json as ``relevant: false`` but are not
listed on YouTube, because the episode's information did not come from them.

``require_sources`` raises :class:`MissingSourcesError` when nothing usable is left,
unless ``allow_missing=True`` or env ``GAKU_ALLOW_NO_SOURCES=1``.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

YOUTUBE_DESCRIPTION_LIMIT = 5000
SOURCES_HEADER = "Sources:"
_MD_LINK = re.compile(r"\[((?:[^\[\]\n]|\[[^\]\n]*\]){2,300})\]\((https?://[^\s)]+)\)")
_SNIPPET = re.compile(r"^\s{2,}-\s+(.+)$")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.]*", re.I)
_STOP = set("""a an and are as at be best by can com complete do does for free from get guide guides how
in into is it its learn learning more new of on or our out resource resources the their this
to tutorial tutorials use using what when where which why with you your 2023 2024 2025 2026
deep dive technical beginner beginners friendly works work introduction intro basics overview
article articles docs documentation official top ultimate""".split())


class MissingSourcesError(RuntimeError):
    """Raised when an upload would go out without any sources."""


# ---------------------------------------------------------------- capture

def _clean_title(title: str) -> str:
    t = re.sub(r"\s+", " ", str(title or "")).strip(" *_-\u2014\u2013")
    return t.replace("<", "").replace(">", "")  # YouTube rejects angle brackets


def normalize_url(url: str) -> str:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = p.netloc.lower().removeprefix("www.")
    path = p.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, p.query, ""))


def parse_markdown_links(text: str, *, origin: str = "results.md") -> list[dict[str, Any]]:
    """Real ``[title](url)`` links from a research markdown file (+ following snippet)."""
    out: list[dict[str, Any]] = []
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        for m in _MD_LINK.finditer(line):
            src: dict[str, Any] = {"title": _clean_title(m.group(1)), "url": m.group(2).rstrip(".,;"),
                                   "kind": "web", "origin": origin}
            if i + 1 < len(lines) and (sm := _SNIPPET.match(lines[i + 1])):
                src["snippet"] = sm.group(1).strip()[:400]
            out.append(src)
    return out


def parse_document_source(path: Path) -> dict[str, Any] | None:
    """A source article/PDF extracted to markdown: title from its heading, URL if present."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    head = next((ln for ln in text.splitlines() if ln.strip()), "")
    title = re.sub(r"^#+\s*(Source:\s*)?", "", head).strip()
    title = re.sub(r"\.pdf$", "", title, flags=re.I).strip()
    if not title:
        return None
    publisher = "Harvard Business Review" if re.search(r"\bHBR\b|hbr\.org|Harvard Business", text[:20000]) else None
    title = re.sub(r"\s*\(HBR\)\s*$", "", title)
    urls = [u.rstrip(".,;") for u in _URL.findall(text[:20000])]
    hbr = [u for u in urls if "hbr.org/" in u]
    src: dict[str, Any] = {"title": _clean_title(title + (f" ({publisher})" if publisher else "")),
                           "url": (hbr or [None])[0], "kind": "document", "origin": Path(path).name}
    return src


def _episode_tokens(episode: dict[str, Any] | None) -> set[str]:
    ep = episode or {}
    parts = [str(ep.get("topic") or ""), str(ep.get("thumbnail_text") or "")]
    parts += [str(k) for k in ep.get("keywords") or []]
    parts += [str(s.get("title") or "") for s in ep.get("segments") or [] if isinstance(s, dict)]
    return _tokens(" ".join(parts))


def _tokens(text: str) -> set[str]:
    toks = set()
    for w in _WORD.findall((text or "").lower()):
        w = w.strip(".")
        if len(w) < 2 or w in _STOP:
            continue
        toks.add(w[:-1] if len(w) > 4 and w.endswith("s") else w)
    return toks


def mark_relevance(sources: list[dict[str, Any]], episode: dict[str, Any] | None) -> list[dict[str, Any]]:
    want = _episode_tokens(episode)
    for s in sources:
        if s.get("kind") == "document" or "relevant" in s and s.get("origin") == "episode":
            s.setdefault("relevant", True)
            continue
        overlap = sorted(_tokens(f"{s.get('title', '')} {s.get('snippet', '')}") & want)
        s["relevant"] = bool(overlap) if want else True
        s["matched_terms"] = overlap[:8]
    return sources


def dedupe(sources: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for s in sources:
        title = _clean_title(s.get("title") or "")
        url = (s.get("url") or "").strip() or None
        if url and not re.match(r"^https?://", url):
            url = None
        if not title and not url:
            continue
        key = normalize_url(url) if url else "title:" + title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({**s, "title": title or url, "url": url})
    return out


def capture_sources(run_dir: Path | str | None, episode: dict[str, Any] | None = None, *,
                    research_text: str | None = None, extra: Iterable[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """Collect real sources from the run's research artifacts (no network, no LLM)."""
    found: list[dict[str, Any]] = []
    for s in (episode or {}).get("sources") or []:
        if isinstance(s, dict):
            found.append({**s, "origin": s.get("origin") or "episode"})
    found += list(extra)
    rd = Path(run_dir) if run_dir else None
    if rd is not None:
        sj = rd / "sources.json"
        if sj.is_file():
            try:
                data = json.loads(sj.read_text(encoding="utf-8"))
                found += [s for s in (data.get("sources") if isinstance(data, dict) else data) or []
                          if isinstance(s, dict)]
            except (OSError, ValueError):
                pass
        for doc in [rd / "hbr_source.md", *sorted((rd / "_source_pdfs").glob("*.md"))]:
            if doc.is_file() and (d := parse_document_source(doc)):
                found.append(d)
        if research_text is None and (rd / "results.md").is_file():
            research_text = (rd / "results.md").read_text(encoding="utf-8", errors="ignore")
    if research_text:
        found += parse_markdown_links(research_text)
    return mark_relevance(dedupe(found), episode)


def write_sources_json(run_dir: Path | str, sources: list[dict[str, Any]]) -> Path:
    path = Path(run_dir) / "sources.json"
    payload = {"note": "Captured from research artifacts; relevant=false entries are not listed on YouTube.",
               "sources": sources}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def listed_sources(sources: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in sources if s.get("relevant", True)]


# ---------------------------------------------------------------- description

def _source_line(s: dict[str, Any]) -> str:
    title, url = s.get("title") or "", s.get("url")
    if url and title and title != url:
        return f"- {title}: {url}"
    return f"- {url or title}"


def format_sources_section(sources: list[dict[str, Any]], budget: int) -> str:
    """'Sources:' block that fits in ``budget`` chars; drops whole lines, never cuts URLs."""
    lines = [_source_line(s) for s in sources]
    for keep in range(len(lines), 0, -1):
        more = len(lines) - keep
        tail = [f"(+{more} more source{'s' if more != 1 else ''} not shown)"] if more else []
        block = "\n".join([SOURCES_HEADER, *lines[:keep], *tail])
        if len(block) <= budget:
            return block
    return ""


def require_sources(sources: list[dict[str, Any]], *, allow_missing: bool = False, context: str = "") -> None:
    if listed_sources(sources):
        return
    if allow_missing or os.environ.get("GAKU_ALLOW_NO_SOURCES", "").strip() in {"1", "true", "yes"}:
        print(f"[sources] WARNING: uploading without sources{f' ({context})' if context else ''} "
              "because the no-sources override is set.")
        return
    raise MissingSourcesError(
        f"No usable sources for this episode{f' ({context})' if context else ''}. Every GAKU upload must "
        "list where its information came from. Add real sources to <run>/sources.json or "
        "episode['sources'], or pass allow_no_sources=True / set GAKU_ALLOW_NO_SOURCES=1 to override.")


def has_sources_section(description: str) -> bool:
    return bool(re.search(r"(?m)^Sources:\s*$", description or ""))


def append_sources(description: str, sources: list[dict[str, Any]], *,
                   reserve: int = 0, limit: int = YOUTUBE_DESCRIPTION_LIMIT) -> str:
    """Append (or replace) the Sources section; total stays <= limit - reserve."""
    text = (description or "").rstrip()
    if has_sources_section(text):  # replace an existing block (re-runs)
        text = re.split(r"(?m)^Sources:\s*$", text, maxsplit=1)[0].rstrip()
    listed = listed_sources(sources)
    if not listed:
        return text[: limit - reserve]
    budget = limit - reserve - len(text) - 2
    if budget < 200:  # trim the body before ever dropping all sources
        text = text[: max(0, limit - reserve - 1200)].rstrip()
        budget = limit - reserve - len(text) - 2
    block = format_sources_section(listed, budget)
    return f"{text}\n\n{block}" if block and text else (block or text)


def build_description(base: str, run_dir: Path | str | None, episode: dict[str, Any] | None, *,
                      allow_no_sources: bool = False, reserve: int = 120) -> tuple[str, list[dict[str, Any]]]:
    sources = capture_sources(run_dir, episode)
    require_sources(sources, allow_missing=allow_no_sources, context=str((episode or {}).get("topic") or run_dir or ""))
    return append_sources(base, sources, reserve=reserve), sources
