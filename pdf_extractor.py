"""PDF -> structured chapter JSON extractor for the GAKU audiobook pipeline.

Walks a PDF page-by-page, groups spans into chapters by sniffing visually
distinct headings (large font, bold, or chapter-pattern lines), and strips
non-audible content (footnote markers, figure refs, page numbers, URLs).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable

import fitz  # pymupdf


WPM = 165

CHAPTER_PATTERNS = [
    re.compile(r"^\s*chapter\s+([ivxlcdm]+|\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\b", re.I),
    re.compile(r"^\s*part\s+([ivxlcdm]+|\d+)\b", re.I),
    re.compile(r"^\s*section\s+(\d+)\b", re.I),
    re.compile(r"^\s*([ivxlcdm]+)\.\s+\S", re.I),  # roman numeral lead
]

NON_AUDIBLE_PATTERNS = [
    re.compile(r"\[\s*\d+\s*\]"),                           # [1] footnote markers
    re.compile(r"\(\s*see\s+page\s+\d+\s*\)", re.I),        # (see page 12)
    re.compile(r"\bsee\s+page\s+\d+\b", re.I),
    re.compile(r"\bfigure\s+\d+(\.\d+)?\b", re.I),
    re.compile(r"\btable\s+\d+(\.\d+)?\b", re.I),
    re.compile(r"https?://\S+"),
    re.compile(r"www\.\S+"),
]

PAGE_NUM_LINE = re.compile(r"^\s*\d{1,4}\s*$")


def looks_like_heading(text: str, size: float, flags: int, max_size: float) -> bool:
    """A span looks like a chapter heading if it's visually large, or matches
    a known chapter-pattern, or is a short ALL-CAPS line."""
    stripped = text.strip()
    if not stripped or len(stripped) > 120:
        return False

    if size >= 16 and size >= max_size * 0.85:
        return True

    is_bold = bool(flags & 16)  # pymupdf bold flag
    for pat in CHAPTER_PATTERNS:
        if pat.match(stripped):
            return True

    if is_bold and len(stripped) < 60:
        if stripped.upper() == stripped and any(c.isalpha() for c in stripped):
            return True

    if len(stripped) < 60 and stripped.upper() == stripped and any(c.isalpha() for c in stripped):
        letters = [c for c in stripped if c.isalpha()]
        if letters:
            return True

    return False


def clean_audible(text: str) -> str:
    """Drop non-audible artifacts and normalize whitespace."""
    out = text

    # Fix hyphenated line breaks: "exam-\nple" -> "example"
    out = re.sub(r"(\w)-\n(\w)", r"\1\2", out)

    for pat in NON_AUDIBLE_PATTERNS:
        out = pat.sub(" ", out)

    # Drop bare page-number lines.
    out_lines = [ln for ln in out.splitlines() if not PAGE_NUM_LINE.match(ln)]
    out = "\n".join(out_lines)

    # Collapse repeated whitespace.
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def iter_page_spans(page: fitz.Page) -> Iterable[dict]:
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            size = max((s.get("size", 0) for s in spans), default=0)
            flags = spans[0].get("flags", 0)
            yield {"text": text, "size": size, "flags": flags}


def detect_doc_max_font(doc: fitz.Document) -> float:
    sizes: list[float] = []
    for page in doc:
        for span in iter_page_spans(page):
            sizes.append(span["size"])
    if not sizes:
        return 12.0
    sizes.sort()
    # Use the 99th percentile so a single oversized cover word doesn't skew it.
    idx = max(0, int(len(sizes) * 0.99) - 1)
    return sizes[idx]


def extract_book_unchaptered(pdf_path: str, chunk_pages: int) -> dict:
    """Bypass heading detection entirely. Group every `chunk_pages` consecutive
    pages into one neutral chunk. Useful when the heading heuristic is unreliable
    (e.g., scanned PDFs with all-caps page headers that get misread as chapters)."""
    doc = fitz.open(pdf_path)

    title = (doc.metadata.get("title") or "").strip()
    if not title:
        title = Path(pdf_path).stem

    chapters: list[dict] = []
    chunk_num = 0
    n_pages = len(doc)
    for start in range(0, n_pages, chunk_pages):
        end = min(start + chunk_pages, n_pages)
        parts: list[str] = []
        for page_idx in range(start, end):
            for span in iter_page_spans(doc[page_idx]):
                parts.append(span["text"])
        chunk_num += 1
        chapters.append({
            "chapter_num": chunk_num,
            "title": f"Pages {start + 1} to {end}",
            "text": clean_audible("\n".join(parts)),
            "page_start": start + 1,
            "page_end": end,
        })

    doc.close()
    return {"title": title, "chapters": chapters}


def extract_book(pdf_path: str) -> dict:
    doc = fitz.open(pdf_path)
    max_font = detect_doc_max_font(doc)

    # Title: try metadata first, else the largest text on page 1.
    title = (doc.metadata.get("title") or "").strip()
    if not title and len(doc) > 0:
        first_spans = list(iter_page_spans(doc[0]))
        if first_spans:
            biggest = max(first_spans, key=lambda s: s["size"])
            title = biggest["text"]
    if not title:
        title = Path(pdf_path).stem

    chapters: list[dict] = []
    current: dict | None = None
    chapter_num = 0

    for page_idx, page in enumerate(doc):
        page_no = page_idx + 1
        page_buffer: list[str] = []

        for span in iter_page_spans(page):
            text = span["text"]
            if looks_like_heading(text, span["size"], span["flags"], max_font):
                # Flush buffered text into the prior chapter before starting a new one.
                if current is not None and page_buffer:
                    current["text"] += "\n" + "\n".join(page_buffer)
                    current["page_end"] = page_no
                    page_buffer = []

                if current is not None:
                    current["text"] = clean_audible(current["text"])
                    chapters.append(current)

                chapter_num += 1
                current = {
                    "chapter_num": chapter_num,
                    "title": text.strip(),
                    "text": "",
                    "page_start": page_no,
                    "page_end": page_no,
                }
            else:
                page_buffer.append(text)

        if current is not None and page_buffer:
            current["text"] += "\n" + "\n".join(page_buffer)
            current["page_end"] = page_no

    if current is not None:
        current["text"] = clean_audible(current["text"])
        chapters.append(current)

    # If we found nothing chapter-like, treat the whole book as one chapter.
    if not chapters:
        full_text = "\n".join(
            "\n".join(s["text"] for s in iter_page_spans(p)) for p in doc
        )
        chapters.append({
            "chapter_num": 1,
            "title": title,
            "text": clean_audible(full_text),
            "page_start": 1,
            "page_end": len(doc),
        })

    doc.close()

    return {"title": title, "chapters": chapters}


def summarize(book: dict) -> str:
    total_chapters = len(book["chapters"])
    total_words = sum(len(c["text"].split()) for c in book["chapters"])
    minutes = total_words / WPM
    return (
        f"Title: {book['title']}\n"
        f"Chapters: {total_chapters}\n"
        f"Total words: {total_words:,}\n"
        f"Estimated duration @ {WPM} WPM: {minutes:.1f} min ({minutes/60:.2f} h)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract a PDF book into structured chapter JSON.")
    parser.add_argument("--pdf", required=True, help="Path to source PDF")
    parser.add_argument("--out", required=True, help="Path to write chapters JSON")
    parser.add_argument(
        "--no-chapters",
        action="store_true",
        help="Skip heading detection. Treat the PDF as continuous text and emit one chunk per --chunk-pages pages.",
    )
    parser.add_argument(
        "--chunk-pages",
        type=int,
        default=10,
        help="When --no-chapters is set, number of consecutive pages per chunk (default: 10).",
    )
    args = parser.parse_args()

    if args.no_chapters:
        book = extract_book_unchaptered(args.pdf, max(1, args.chunk_pages))
    else:
        book = extract_book(args.pdf)
    Path(args.out).write_text(json.dumps(book, indent=2, ensure_ascii=False), encoding="utf-8")

    print(summarize(book), file=sys.stderr)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
