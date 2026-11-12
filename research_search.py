"""Free, relevance-filtered research search for GAKU episodes (replaces gaku.py's queries).

Why: gaku.py searched the *entire* title as one quoted phrase plus "best resources"
(e.g. '"How 3D Model Animation Works: A Technical, Beginner-Friendly Deep Dive" best
resources'). That exact phrase exists nowhere, so the search engine dropped it and
matched only "best resources", returning job boards, apps and "free learning
resources" lists. Nothing checked relevance, and the junk went straight to Gemini.

Now:
1. ``core_phrase`` strips the subtitle and filler ("How ... Works") -> "3D Model Animation".
2. Wikipedia's free MediaWiki API finds the on-topic encyclopedia pages and their titles
   become facet queries ("Skeletal animation", "Morph target animation"), alongside
   optional keywords / segment titles.
3. Web hits come from ddgs (free metasearch), trying several backends because single
   backends rate-limit; DuckDuckGo alone often returns nothing.
4. Every hit must share >= 2 content words with the topic + facets, including >= 1
   core word; job boards / app logins / social are dropped; <= 2 per domain.
5. Output: results.md (research notes for Gemini, incl. Wikipedia intro extracts with
   their URLs) + research_sources.json (the same sources, marked relevant), which
   episode_sources.capture_sources() turns into sources.json and the YouTube list.

No paid APIs, no LLM calls.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from episode_sources import _tokens  # same tokenizer as the YouTube Sources filter

UA = "GAKU-research/2.0 (PROJECT GAKU podcast pipeline; gavinbolin3411@gmail.com)"
WEB_BACKENDS = ("bing", "yahoo", "duckduckgo", "brave", "mojeek")
MIN_SHARED = 2
MAX_SOURCES = 15
PER_DOMAIN = 2
BLOCKED_HOSTS = ("bdjobs.com", "indeed.com", "glassdoor.com", "linkedin.com", "pinterest.", "facebook.com",
                 "instagram.com", "tiktok.com", "x.com", "twitter.com", "amazon.", "ebay.", "aliexpress.",
                 "clio.com", "fiverr.com", "upwork.com", "grokipedia.com", "worldmetrics.org", "scribd.com",
                 "coursehero.com", "chegg.com",
                 # encyclopedia mirrors / dictionaries: copies of a Wikipedia page, not extra sources
                 "wiktionary.org", "handwiki.org", "explained.today", "freedict", "fandom.com", "wikiwand.com",
                 "dbpedia.org", "alchetron.com", "everybodywiki.com", "wikizero.com", "thefreedictionary.com",
                 "eonsr.com")
# A web page counts as an information source only if it reads like an explanation.
_EXPLAIN = re.compile(r"\b(what|how|why|guide|explained|explain\w*|introduction|intro|tutorial|understanding|"
                      r"definition|defined|overview|basics|beginner\w*|process|pipeline|works?|learn\w*|"
                      r"principles?|fundamentals?|history|technique\w*|types?|deep dive|primer|course|lecture|"
                      r"manual|documentation|docs|handbook|paper|research|study)\b", re.I)
_SUBTITLE_FILLER = re.compile(r"\b(a|an|the|technical|beginner[- ]friendly|beginners?|deep dive|guide|"
                              r"explained|introduction|overview|primer|for|and|in|of|to|your|complete)\b", re.I)
# Storefront / tool landing pages: they mention the topic but explain nothing.
_COMMERCE = re.compile(r"\b(download|downloads|free (&|and) premium|buy|price|pricing|shop|store|marketplace|"
                       r"generator|maker|online tool|rank(ed|ings?)|top \d+|for sale|coupon|sign ?up|log ?in)\b", re.I)
_TRUSTED = (".edu", "wikipedia.org", "docs.", "developer.", "developers.", "khronos.org", "mozilla.org",
            "readthedocs.io", "github.com", ".gov", "acm.org", "ieee.org", "arxiv.org", "britannica.com")
_LEAD = re.compile(r"^(how|what|why|when|where|who|inside|understanding|explaining|exploring|the|a|an)\s+(is|are|does|do|to)?\s*", re.I)
_TRAIL = re.compile(r"\s+(works?|explained|matters?|really works|actually works)$", re.I)
_GENERIC_SEGMENTS = re.compile(r"^(hook|intro(duction)?|recap|close|closing( thoughts)?|key takeaways|outro|summary|"
                               r"wrap[- ]?up|conclusion|q&a)$", re.I)


@dataclass
class Source:
    title: str
    url: str
    snippet: str = ""
    kind: str = "web"
    query: str = ""
    matched_terms: list[str] = field(default_factory=list)
    relevant: bool = True
    origin: str = "research"


def core_phrase(topic: str) -> str:
    t = re.split(r"\s*(?::|\s[-\u2013\u2014|]\s|\?)\s*", (topic or "").strip(), maxsplit=1)[0]
    prev = None
    while prev != t:
        prev = t
        t = _LEAD.sub("", t).strip()
        t = _TRAIL.sub("", t).strip()
    return t or (topic or "").strip()


def clean_title(title: str, limit: int = 110) -> str:
    """Some backends glue several result titles together; keep the first one."""
    t = re.sub(r"\s+", " ", title or "").strip()
    m = re.search(r"(?<=[a-z0-9)\]])(?=[A-Z][a-z]+ [A-Za-z])", t[20:])  # '...ProcessThe ultimate'
    if len(t) > 80 and m:
        t = t[: 20 + m.start()]
    t = re.split(r"\s*(?:\.\.\.|…)\s*", t)[0] if len(t) > 80 else t
    return t[:limit].rstrip(" -|:")


def _clean_segment(title: str) -> str:
    t = re.sub(r"[^\w\s+#.-]", " ", title or "")
    t = re.sub(r"\s+", " ", t).strip()
    return "" if not t or _GENERIC_SEGMENTS.match(t) else t


# ---------------------------------------------------------------- backends

def _get_json(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https hosts
        return json.loads(r.read().decode("utf-8"))


def wikipedia_search(query: str, limit: int = 6) -> list[Source]:
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": query, "srlimit": limit, "format": "json",
         "srprop": "snippet"})
    try:
        data = _get_json(url)
    except Exception as exc:  # network hiccup -> just fewer sources
        print(f"[research] wikipedia search failed for {query!r}: {exc}", file=sys.stderr)
        return []
    out = []
    for hit in (data.get("query") or {}).get("search") or []:
        title = hit.get("title") or ""
        snippet = re.sub(r"<[^>]+>", "", hit.get("snippet") or "")
        out.append(Source(title=f"{title} - Wikipedia",
                          url="https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                          snippet=re.sub(r"\s+", " ", snippet).strip(), kind="encyclopedia", query=query))
    return out


def wikipedia_extracts(titles: Iterable[str], chars: int = 1200) -> dict[str, str]:
    titles = [t for t in titles if t]
    if not titles:
        return {}
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1, "exchars": chars,
         "titles": "|".join(titles[:5]), "format": "json", "redirects": 1})
    try:
        pages = (_get_json(url).get("query") or {}).get("pages") or {}
    except Exception as exc:
        print(f"[research] wikipedia extracts failed: {exc}", file=sys.stderr)
        return {}
    return {p.get("title", ""): (p.get("extract") or "").strip() for p in pages.values() if p.get("extract")}


def web_search(query: str, limit: int = 8, backends: Iterable[str] = WEB_BACKENDS) -> list[Source]:
    try:
        from ddgs import DDGS
    except ImportError:  # pragma: no cover
        from duckduckgo_search import DDGS  # type: ignore
    for be in backends:
        try:
            with DDGS() as d:
                hits = list(d.text(query, max_results=limit, backend=be))
        except Exception:
            hits = []
        if hits:
            return [Source(title=clean_title(h.get("title") or "") or h.get("href", ""), url=h.get("href") or "",
                           snippet=(h.get("body") or "").strip(), query=query) for h in hits if h.get("href")]
        time.sleep(0.4)
    print(f"[research] no web results for {query!r}", file=sys.stderr)
    return []


# ---------------------------------------------------------------- relevance

def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")


def blocked_url(url: str) -> bool:
    host = _host(url)
    if not url.startswith(("http://", "https://")) or not host:
        return True
    if host.startswith(("app.", "login.", "accounts.", "auth.")):
        return True
    return any(b in host for b in BLOCKED_HOSTS)


def score(src: Source, core: set[str], facets: set[str]) -> tuple[int, list[str]]:
    toks = _tokens(f"{src.title} {src.snippet}")
    core_hit = toks & core
    shared = sorted(toks & (core | facets))
    if not core_hit or len(shared) < MIN_SHARED or _COMMERCE.search(src.title):
        return 0, shared
    host = _host(src.url)
    trusted = any(t in host or host.endswith(t) for t in _TRUSTED)
    if src.kind == "web" and not trusted and not _EXPLAIN.search(f"{src.title} {src.snippet}"):
        return 0, shared  # tool/product landing page
    return (2 * len(core_hit) + len(shared) + (2 if src.kind == "encyclopedia" else 0)
            + (2 if trusted else 0) - (1 if "youtube.com" in host else 0)), shared


def filter_rank(cands: Iterable[Source], core: set[str], facets: set[str], *,
                limit: int = MAX_SOURCES) -> tuple[list[Source], list[Source]]:
    seen: set[str] = set()
    kept: list[tuple[int, int, Source]] = []
    dropped: list[Source] = []
    for i, s in enumerate(cands):
        key = re.sub(r"^https?://(www\.)?", "", urllib.parse.unquote(s.url)).rstrip("/").split("#")[0].lower()
        if key in seen:
            continue
        seen.add(key)
        sc, shared = score(s, core, facets)
        s.matched_terms = shared[:8]
        if blocked_url(s.url) or sc == 0 or re.match(r"(list|outline|index) of ", s.title, re.I):
            s.relevant = False
            dropped.append(s)
            continue
        kept.append((sc, -i, s))
    kept.sort(key=lambda x: (x[0], x[1]), reverse=True)
    per: dict[str, int] = {}
    out: list[Source] = []
    for _, _, s in kept:
        h = _host(s.url)
        if per.get(h, 0) >= (3 if h.endswith("wikipedia.org") else PER_DOMAIN):
            continue
        per[h] = per.get(h, 0) + 1
        out.append(s)
        if len(out) >= limit:
            break
    return out, dropped


# ---------------------------------------------------------------- pipeline

def subtitle_parts(topic: str) -> list[str]:
    """'X: Embeddings, Indexes, and Similarity Search' -> ['Embeddings', 'Indexes', 'Similarity Search']."""
    parts = re.split(r"\s*(?::|\s[-\u2013\u2014|]\s|\?)\s*", (topic or "").strip(), maxsplit=1)
    if len(parts) < 2:
        return []
    out = []
    for piece in re.split(r",|\band\b|&|/", parts[1]):
        piece = re.sub(r"\s+", " ", _SUBTITLE_FILLER.sub(" ", piece)).strip(" -")
        if len(piece) > 2:
            out.append(piece)
    return out


def build_queries(topic: str, facets: Iterable[str] = (), keywords: Iterable[str] = (),
                  segment_titles: Iterable[str] = (), max_queries: int = 9) -> list[str]:
    """Short keyword queries (never the whole quoted title). ``facets`` are only used
    for relevance scoring, not searched: obscure Wikipedia titles pull in mirrors."""
    core = core_phrase(topic)
    qs = [core, f"{core} explained", f"how {core.lower()} works", f"{core} beginner guide"]
    qs += [f"{core} {p}" for p in subtitle_parts(topic)[:3]]
    qs += [f"{core} {k}" for k in list(keywords)[:3] if k]
    qs += [f"{core} {s}" for s in (_clean_segment(t) for t in segment_titles) if s][:3]
    seen, out = set(), []
    for q in qs:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:max_queries]


def research(topic: str, *, keywords: Iterable[str] = (), segment_titles: Iterable[str] = (),
             web: Callable[[str, int], list[Source]] = web_search,
             wiki: Callable[[str, int], list[Source]] = wikipedia_search,
             extracts: Callable[[Iterable[str]], dict[str, str]] = wikipedia_extracts,
             pause: float = 0.6) -> dict[str, Any]:
    keywords, segment_titles = list(keywords), list(segment_titles)
    core = core_phrase(topic)
    core_t = _tokens(core)
    wiki_hits = wiki(core, 8)
    facet_titles = [t for t in (w.title.removesuffix(" - Wikipedia") for w in wiki_hits)
                    if _tokens(t) & core_t and "(" not in t and not t.lower().startswith(("list of", "outline of"))]
    facets_t = _tokens(" ".join([*facet_titles[:5], *keywords, *map(_clean_segment, segment_titles)])) - core_t
    queries = build_queries(topic, facet_titles, keywords, segment_titles)
    cands: list[Source] = list(wiki_hits)
    for q in queries:
        cands += web(q, 8)
        if pause:
            time.sleep(pause)
    kept, dropped = filter_rank(cands, core_t, facets_t)
    wiki_kept = [s.title.removesuffix(" - Wikipedia") for s in kept if s.kind == "encyclopedia"][:3]
    notes = extracts(wiki_kept) if wiki_kept else {}
    return {"topic": topic, "core": core, "queries": queries, "facets": facet_titles[:5],
            "sources": kept, "dropped": dropped, "notes": notes}


def render_markdown(result: dict[str, Any]) -> str:
    lines = ["# GAKU - Research Sources", "", f"## {result['topic']}", "",
             f"_Search core: {result['core']}; {len(result['sources'])} on-topic sources "
             f"(filtered from {len(result['sources']) + len(result['dropped'])})._", ""]
    groups = [("Reference", "encyclopedia"), ("Article", "web")]
    for label, kind in groups:
        items = [s for s in result["sources"] if s.kind == kind]
        if not items:
            continue
        lines += [f"### {label}", ""]
        for s in items:
            lines.append(f"- **[{s.title}]({s.url})** - _{label}_")
            if s.snippet:
                lines.append(f"  - {s.snippet[:400]}")
        lines.append("")
    if result.get("notes"):
        lines += ["### Reference notes (Wikipedia introductions)", ""]
        for title, text in result["notes"].items():
            url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
            lines += [f"#### {title} ({url})", "", text, ""]
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(result: dict[str, Any], out_md: Path | str) -> tuple[Path, Path]:
    out_md = Path(out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_markdown(result), encoding="utf-8")
    sj = out_md.with_name("research_sources.json")
    payload = {"topic": result["topic"], "core": result["core"], "queries": result["queries"],
               "facets": result["facets"], "sources": [asdict(s) for s in result["sources"]],
               "dropped": [asdict(s) for s in result["dropped"]]}
    sj.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_md, sj


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Free, relevance-filtered research search (no LLM).")
    ap.add_argument("--topic", required=True)
    ap.add_argument("--out", default="results.md")
    ap.add_argument("--keywords", default="", help="Comma-separated extra facets")
    ap.add_argument("--episode", help="episode.json to borrow keywords + segment titles from")
    ap.add_argument("--min-sources", type=int, default=5)
    args = ap.parse_args(argv)
    kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
    segs: list[str] = []
    if args.episode:
        ep = json.loads(Path(args.episode).read_text(encoding="utf-8"))
        kws += list(ep.get("keywords") or [])
        segs = [s.get("title") or "" for s in ep.get("segments") or []]
    res = research(args.topic, keywords=kws, segment_titles=segs)
    md, sj = write_outputs(res, args.out)
    print(f"[research] {len(res['sources'])} on-topic sources ({len(res['dropped'])} dropped) -> {md}, {sj}")
    for s in res["sources"]:
        print(f"  - {s.title}: {s.url}")
    if len(res["sources"]) < args.min_sources:
        print(f"[research] WARNING: fewer than {args.min_sources} on-topic sources", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
