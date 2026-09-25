"""broll_queries.py -- article-grounded Pexels B-roll queries + photo picking.

Approved 2026-09-25 (daily review items 3, 8, 9):

* Queries come from the words spoken in each B-roll window (plus the
  segment text), mapped to concrete industry / activity nouns. Single
  generic words ("AI", "Global") and stopword fragments ("Un") are rejected.
* Knowledge / chat bots and LLM "agents" never get robot imagery: the query
  generator never emits robot words, and Pexels results whose alt text
  mentions robots / androids / robot arms are filtered out unless the beat is
  literally about industrial robots.
* Text-heavy word-art stock ("STRATEGY" letter blocks, signs, scrabble
  tiles) is filtered via the Pexels alt text and abstract-concept query
  words, so no giant watermark words fight the karaoke band.
* PexelsPicker tracks every used photo id per episode (pexels_used.json)
  so the same stock image is never reused across chapters / backdrops.

Everything here is deterministic and works without Gemini. Gemini
suggestions (gemini_image_query) are validated with `validate_query` and
fall back to `local_query_candidates` when rejected.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Query validation
# ---------------------------------------------------------------------------

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "for", "to", "in", "on", "at",
    "by", "with", "from", "as", "is", "are", "was", "be", "it", "its", "this",
    "that", "these", "those", "into", "over", "about", "un", "de", "la", "le",
    "el", "les", "des", "so", "very", "more", "most", "just", "like", "what",
    "how", "why", "who", "we", "you", "they", "our", "their", "your",
}

# Words that are too abstract / generic to carry a query on their own.
VAGUE_WORDS = {
    "ai", "a.i", "gen", "genai", "generative", "artificial", "intelligence",
    "tech", "technology", "future", "business", "innovation", "digital",
    "empty", "team", "office", "project", "data", "people", "work", "meeting",
    "corporate", "abstract", "global", "enterprise", "strategic", "strategy",
    "integrated", "integration", "solution", "solutions", "concept", "idea",
    "ideas", "success", "growth", "value", "impact", "change", "system",
    "systems", "model", "models", "process", "shift", "scale", "new",
    "modern", "company", "companies", "organization", "organizations",
    "leader", "leaders", "leadership", "transformation", "individual",
    "experiment", "experiments", "network", "cloud", "platform",
}

# Never in a query (LLM != robot; no sci-fi metaphors).
BANNED_QUERY_TOKENS = {
    "robot", "robots", "robotic", "robotics", "android", "androids",
    "humanoid", "cyborg", "automaton", "terminator", "hologram",
    "holographic", "brain", "neural", "matrix", "futuristic",
}

# Abstract nouns that on Pexels mostly return word-art (letter blocks,
# neon signs, sticky notes with the word written on them).
TEXT_ART_QUERY_TOKENS = {
    "strategy", "strategic", "success", "idea", "concept", "word", "words",
    "text", "letters", "letter", "sign", "quote", "motivation", "goal",
    "goals", "vision", "mission", "plan", "planning",
}

# Pexels alt-text rejects.
ROBOT_ALT_RE = re.compile(
    r"\b(robot\w*|android|humanoid|cyborg|robotic arm|mechanical arm|"
    r"industrial arm|automaton|artificial intelligence concept)\b",
    re.IGNORECASE,
)
TEXT_ART_ALT_RE = re.compile(
    r"\b(text|word|words|letter|letters|lettering|written|writing on|"
    r"typography|scrabble|tiles? spelling|spelling|inscription|signage|"
    r"neon sign|sign that says|quote|alphabet|wooden blocks?|cubes? with|"
    r"sticky notes? with|message)\b",
    re.IGNORECASE,
)

INDUSTRIAL_ROBOT_CONTEXT_RE = re.compile(
    r"\b(factory robots?|industrial robots?|robotic arms?|assembly line robots?|"
    r"warehouse robots?|manufacturing robots?)\b",
    re.IGNORECASE,
)
LLM_CONTEXT_RE = re.compile(
    r"\b(chat ?bot|bot|bluebot|assistant|copilot|llm|large language model|"
    r"chatgpt|claude|gemini|gpt|agent|agents|agentic|agentforce|knowledge base|"
    r"gen ?ai|generative ai)\b",
    re.IGNORECASE,
)


def _tokens(q: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9.&'-]+", (q or "").lower()) if t]


def validate_query(query: str) -> tuple[bool, str]:
    """Return (ok, reason). Rules: >= 2 meaningful words, no vague-only,
    no stopword fragments, no robot / sci-fi words, no word-art magnets."""
    q = re.sub(r"\s+", " ", (query or "").strip().strip("\"'.`")).strip()
    if not q:
        return False, "empty"
    toks = _tokens(q)
    if len(toks) < 2:
        return False, "single word"
    meaningful = [t for t in toks if t not in STOPWORDS and len(t) >= 3]
    if len(meaningful) < 2:
        return False, "too few meaningful words"
    if any(t in BANNED_QUERY_TOKENS for t in toks):
        return False, "banned (robot / sci-fi metaphor)"
    if any(t in TEXT_ART_QUERY_TOKENS for t in toks):
        return False, "word-art magnet"
    concrete = [t for t in meaningful if t not in VAGUE_WORDS]
    if len(concrete) < 2:
        return False, "vague"
    if len(q) > 60:
        return False, "too long"
    return True, "ok"


def clean_query(query: str) -> str:
    q = (query or "").strip().splitlines()[0] if (query or "").strip() else ""
    q = re.sub(r"^(query|search|answer)\s*:\s*", "", q, flags=re.IGNORECASE)
    q = q.strip().strip("\"'`.!?")
    q = re.sub(r"[^\w\s&'-]", " ", q)
    return re.sub(r"\s+", " ", q).strip().lower()


# ---------------------------------------------------------------------------
# Local article-grounded query generator
# ---------------------------------------------------------------------------

# (pattern, [concrete queries]) -- order matters only for ties.
CONCEPTS: list[tuple[str, re.Pattern[str], list[str]]] = [
    ("airline", re.compile(r"\b(airline|airlines|jetblue|flight|airport|aviation|pilot|cabin crew)\b", re.I),
     ["airport terminal travelers", "airline cabin crew", "airplane on runway", "airport check in counter"]),
    ("beverage", re.compile(r"\b(coca-?cola|beverage|soda|bottl\w+|drinks?)\b", re.I),
     ["soda bottles production line", "beverage cans store shelf", "cold drinks cafe table"]),
    ("localization", re.compile(r"\b(countries|languages|translat\w*|localiz\w*|international|markets)\b", re.I),
     ["world map pins travel", "international colleagues video call", "multilingual team laptop"]),
    ("marketing", re.compile(r"\b(marketing|campaigns?|advertis\w*|brand|creative assets?|content creation|copy)\b", re.I),
     ["marketing team campaign review", "creative agency storyboard", "designers reviewing ad layouts", "billboard city street"]),
    ("pharma", re.compile(r"\b(drug development|pharma\w*|clinical|laborator\w*|johnson\s*&\s*johnson|biotech)\b", re.I),
     ["pharmaceutical lab scientist", "laboratory researcher microscope", "medicine pills production"]),
    ("hr", re.compile(r"\b(hr|human resources|hiring|recruit\w*|policy access|employees? handbook)\b", re.I),
     ["hr manager interview candidate", "employee onboarding session"]),
    ("sales", re.compile(r"\b(sales reps?|salespeople|sales team|selling|sales calls?)\b", re.I),
     ["sales representative client handshake", "sales team phone calls"]),
    ("supply_chain", re.compile(r"\b(supply chain|logistics|warehouse|shipping|inventory|freight)\b", re.I),
     ["warehouse logistics workers", "shipping containers port", "forklift warehouse shelves"]),
    ("customer_service", re.compile(r"\b(customer (?:service|support|issues?|help)|help requests?|call cent\w+|help ?desk|tickets?)\b", re.I),
     ["customer support agent headset", "call center operators computers", "help desk support laptop"]),
    ("knowledge_bot", re.compile(r"\b(knowledge (?:assistant|bot|base)|chat ?bot|bluebot|assistant|self-serve|llm|chatgpt|claude|drafting emails?)\b", re.I),
     ["employee typing laptop chat", "person using chat app laptop", "colleague asking question laptop", "woman typing email laptop"]),
    ("agentic", re.compile(r"\b(agentic|agentforce|agents?|autonom\w+)\b", re.I),
     ["operations team monitoring screens", "professional reviewing workflow laptop", "control room operators screens"]),
    ("unstructured", re.compile(r"\b(unstructured|documents?|emails?|reports?|meeting notes|documentation|pdfs?|text-heavy)\b", re.I),
     ["stacks paper documents desk", "filing cabinet folders office", "person sorting paper files"]),
    ("databases", re.compile(r"\b(databases?|structured data|data flows?|data assets?|data quality|curat\w+|stewards?\w*)\b", re.I),
     ["analyst reviewing spreadsheet", "data analyst dual monitors", "librarian organizing archive shelves"]),
    ("servers", re.compile(r"\b(servers?|infrastructure|data cent\w+|integrat\w+ with multiple systems)\b", re.I),
     ["data center server racks", "it technician server room"]),
    ("dashboards", re.compile(r"\b(dashboards?|analytics|metrics|kpis?|measur\w+|monitor\w*)\b", re.I),
     ["analytics dashboard monitor", "business analyst charts screen", "financial charts laptop screen"]),
    ("finance", re.compile(r"\b(roi|cost|bottom[- ]line|revenue|profit|budget|invest\w*|fund\w*)\b", re.I),
     ["financial report desk calculator", "accountant reviewing budget", "coins stacks finance"]),
    ("governance", re.compile(r"\b(governance|risk|compliance|audit\w*|regulat\w+|controlled)\b", re.I),
     ["auditor reviewing documents", "compliance officer paperwork"]),
    ("collaboration", re.compile(r"\b(collaborat\w+|partners?\w*|cross-functional|business and (?:tech|development)|role clarity|together)\b", re.I),
     ["cross functional team workshop", "colleagues brainstorming whiteboard", "coworkers sticky notes wall", "team huddle standing desk"]),
    ("executive", re.compile(r"\b(c-suite|ceo|executives?|board|decision rights|chief \w+ officer|cmo)\b", re.I),
     ["executives boardroom meeting", "ceo presenting boardroom", "business leaders conference table"]),
    ("workflow", re.compile(r"\b(workflows?|process\w*|mapp\w+|documentation norms|procedures?)\b", re.I),
     ["process mapping whiteboard", "flowchart sticky notes wall", "team mapping workflow whiteboard"]),
    ("pilot_rollout", re.compile(r"\b(pilot\w*|prototype|rollout|rolling it out|iterat\w+|week by week|weekly|feedback loop)\b", re.I),
     ["product team sprint review", "software testing team laptops", "team reviewing feedback notes"]),
    ("training", re.compile(r"\b(training|skills|upskill\w*|learning|mindsets?|workshop)\b", re.I),
     ["corporate training workshop", "employees learning seminar"]),
    ("software", re.compile(r"\b(developers?|development teams?|engineers?|coding|software|it projects?|tech teams?)\b", re.I),
     ["software developers coding office", "programmers pair coding"]),
    ("productivity", re.compile(r"\b(productivity|time sav\w+|efficien\w+|faster|speed)\b", re.I),
     ["busy office worker laptop", "stopwatch desk laptop", "focused employee working laptop"]),
    ("retail", re.compile(r"\b(retail|stores?|shoppers?|e-?commerce)\b", re.I),
     ["retail store shoppers", "online shopping packages"]),
    ("health", re.compile(r"\b(hospital|patients?|doctors?|healthcare|clinic)\b", re.I),
     ["doctor tablet hospital", "nurse patient clinic"]),
    ("banking", re.compile(r"\b(bank\w*|insurance|mutual|financial services)\b", re.I),
     ["financial advisor client meeting", "insurance agent paperwork"]),
    ("manufacturing", re.compile(r"\b(factory|manufactur\w+|assembly line|production line)\b", re.I),
     ["factory production line workers", "manufacturing plant engineers"]),
    ("leadership_decisions", re.compile(r"\b(leaders?|priorit\w+|decid\w+|decision|roadmap)\b", re.I),
     ["leadership team conference room", "managers discussing documents", "managers meeting conference table"]),
    ("individual_use", re.compile(r"\b(individual\w*|ad hoc|experiment\w*|employees? (?:use|using|trying)|desktop)\b", re.I),
     ["employee working laptop home", "person typing laptop cafe", "young professional laptop desk"]),
]

EPISODE_FALLBACKS = [
    "colleagues discussing laptop office",
    "business people reviewing documents",
    "team meeting conference room",
    "professional working laptop desk",
    "coworkers whiteboard discussion",
]


def concept_scores(window_text: str, context_text: str = "") -> list[tuple[str, float, list[str]]]:
    """Score concepts by hits in the window (x3) plus the wider context (x1)."""
    out: list[tuple[str, float, list[str]]] = []
    for name, pat, queries in CONCEPTS:
        w = len(pat.findall(window_text or ""))
        c = len(pat.findall(context_text or "")) if context_text else 0
        score = 3.0 * w + 0.5 * min(c, 6)
        if score > 0:
            out.append((name, score, queries))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def local_query_candidates(
    window_text: str,
    context_text: str = "",
    *,
    salt: int = 0,
    recent: Iterable[str] = (),
    limit: int = 4,
) -> list[str]:
    """Ordered, validated fallback chain of concrete queries for one beat."""
    recent_set = {r.lower() for r in recent}
    ranked = concept_scores(window_text, context_text)
    cands: list[str] = []
    for i, (_name, _score, queries) in enumerate(ranked[:4]):
        rot = queries[(salt + i) % len(queries):] + queries[: (salt + i) % len(queries)]
        for q in rot:
            if q not in cands:
                cands.append(q)
    # Prefer ones not used in the last few windows.
    fresh = [q for q in cands if q.lower() not in recent_set]
    stale = [q for q in cands if q.lower() in recent_set]
    ordered = fresh + stale
    fb = EPISODE_FALLBACKS[salt % len(EPISODE_FALLBACKS):] + EPISODE_FALLBACKS[: salt % len(EPISODE_FALLBACKS)]
    for q in fb:
        if q not in ordered:
            ordered.append(q)
    return [q for q in ordered if validate_query(q)[0]][: max(limit, 1)]


def is_llm_context(text: str) -> bool:
    return bool(LLM_CONTEXT_RE.search(text or ""))


def photo_rejected(photo: dict[str, Any], context_text: str = "") -> str | None:
    """Return a reject reason for a Pexels photo, or None when acceptable."""
    alt = str(photo.get("alt") or "")
    url = str(photo.get("url") or photo.get("page_url") or "")
    blob = f"{alt} {url.replace('-', ' ')}"
    if ROBOT_ALT_RE.search(blob) and not INDUSTRIAL_ROBOT_CONTEXT_RE.search(context_text or ""):
        return "robot imagery"
    if TEXT_ART_ALT_RE.search(alt):
        return "text / word-art"
    return None


# ---------------------------------------------------------------------------
# Pexels access with per-episode de-duplication
# ---------------------------------------------------------------------------

CACHE_VERSION = "v2"


class PexelsPicker:
    """Search Pexels (cached) and hand out photos never used before in this
    episode. Used ids persist in <run_dir>/pexels_used.json."""

    def __init__(self, run_dir: Path | None, pexels_key: str | None, *, per_page: int = 20):
        self.run_dir = Path(run_dir) if run_dir else None
        self.key = pexels_key or os.environ.get("PEXELS_API_KEY")
        self.per_page = per_page
        self.cache_path = (self.run_dir / "pexels_cache.json") if self.run_dir else None
        self.used_path = (self.run_dir / "pexels_used.json") if self.run_dir else None
        self.cache: dict[str, Any] = {}
        self.used: dict[str, dict[str, Any]] = {}
        self.api_calls = 0
        self.rejects: list[dict[str, Any]] = []
        if self.cache_path and self.cache_path.exists():
            try:
                self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.cache = {}
        # A fresh manifest build starts a fresh per-episode used set.
        self.used = {}

    # -- persistence -------------------------------------------------------
    def save(self) -> None:
        if self.cache_path:
            try:
                self.cache_path.write_text(json.dumps(self.cache, indent=2), encoding="utf-8")
            except OSError:
                pass
        if self.used_path:
            try:
                self.used_path.write_text(
                    json.dumps({"used_ids": sorted(self.used), "photos": self.used}, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass

    # -- search ------------------------------------------------------------
    def candidates(self, query: str, orientation: str = "landscape") -> list[dict[str, Any]]:
        q = clean_query(query)
        ck = f"{CACHE_VERSION}|{q}|{orientation}"
        hit = self.cache.get(ck)
        if isinstance(hit, list):
            return hit
        if not self.key:
            return []
        try:
            import requests  # noqa: WPS433
        except ImportError:
            print("[broll] requests not installed; no Pexels", file=sys.stderr)
            return []
        try:
            self.api_calls += 1
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": self.key},
                params={"query": q, "per_page": self.per_page, "orientation": orientation},
                timeout=15,
            )
            resp.raise_for_status()
            photos = (resp.json() or {}).get("photos") or []
        except Exception as exc:  # network / quota -- never fatal
            print(f"[broll] pexels search failed for {q!r}: {exc}", file=sys.stderr)
            return []
        out: list[dict[str, Any]] = []
        for p in photos:
            src = p.get("src") or {}
            url = src.get("large2x") or src.get("large")
            if not url:
                continue
            out.append({
                "id": str(p.get("id")),
                "url": url,
                "alt": p.get("alt") or "",
                "photographer": p.get("photographer") or "Pexels",
                "page_url": p.get("url") or "",
            })
        self.cache[ck] = out
        return out

    def pick(
        self,
        queries: list[str],
        *,
        context_text: str = "",
        role: str = "broll",
        orientation: str = "landscape",
    ) -> dict[str, Any] | None:
        """Walk the fallback chain; return first acceptable unused photo."""
        for q in queries:
            ok, _why = validate_query(q)
            if not ok:
                continue
            for photo in self.candidates(q, orientation):
                pid = str(photo.get("id"))
                if pid in self.used:
                    continue
                why = photo_rejected(photo, context_text)
                if why:
                    self.rejects.append({"query": q, "id": pid, "reason": why, "alt": photo.get("alt")})
                    continue
                entry = {**photo, "query": q, "role": role}
                self.used[pid] = {"query": q, "role": role, "url": photo.get("url")}
                return entry
        return None


def window_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


__all__ = [
    "validate_query",
    "clean_query",
    "local_query_candidates",
    "concept_scores",
    "photo_rejected",
    "is_llm_context",
    "PexelsPicker",
    "EPISODE_FALLBACKS",
]
