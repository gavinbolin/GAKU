import React from "react";
import { interpolate } from "remotion";

export interface StatOverlayProps {
  label: string;
  value: string;
  subtext?: string;
  progress: number;
  speakerColor: string;
  /** ms since the overlay appeared (count-up then finishes within 900 ms). */
  localMs?: number;
}

/**
 * Stat value reveal -- dual path per Gavin (2026-09-24):
 *
 *   1) Ranges / multi-number (e.g. "5-10%", "3–5x") → static snap of the full
 *      correct string. Card opacity + scale only. NEVER digit-roll through
 *      wrong intermediate values (Enterprise bug: "5-10%" → "1-10%"…"4-10%").
 *   2) Simple single metrics (efficiency increases, etc.) → whole-number
 *      count-up (alt b): lerp the numeric value and re-format
 *      prefix+number+suffix each frame ("12%" → "27%" → "42%").
 *      NOT digit-slot rolling; never invent wrong prefixes.
 *
 * Gate: isSimpleSingleNumber(value) → countUpDisplay; else → raw value.
 * Root cause of the Enterprise bug: parseNumeric peeled the first number off
 * ranges as num=5 + suffix="-10%", then lerped 0→5. Ranges stay static forever.
 */

// Range detector: hyphen / en-dash / em-dash bounds must stay static forever.
const RANGE_RE = /\d\s*(?:[-–—]|to)\s*\d/i;

/**
 * 2026-09-28 item 6: limits / approximations ("Up to 6.9%", "over $4 billion",
 * "about 40%", "~40%", "10,000+") are NOT exact quantities -- counting up
 * through them shows wrong values, so they always render as a static snap.
 */
export const QUALIFIER_RE =
  /(?:^|\b)(?:up to|about|around|nearly|over|more than|less than|fewer than|almost|roughly|approximately|approx|at least|at most|as much as|as many as|as high as|as low as|under|above|below|close to|upwards of)\b|[~≈<>≤≥]|\d\+|\+\s*$/i;

/** Leading qualifier words, split off so they can render as a small kicker. */
export const LEADING_QUALIFIER_RE =
  /^\s*(up to|about|around|nearly|over|more than|less than|fewer than|almost|roughly|approximately|at least|at most|as much as|as many as|as high as|as low as|under|above|below|close to|upwards of|~|≈)\s*/i;

/** True when the value must never count up (range, limit, approximation, multi-number). */
export const isStaticStat = (raw: string): boolean => {
  const v = (raw || "").trim();
  if (!v) return true;
  if (RANGE_RE.test(v) || QUALIFIER_RE.test(v)) return true;
  return (v.match(/\d[\d,]*(?:\.\d+)?/g) || []).length !== 1;
};

// 2026-09-25: thousands separators are grouping, not decimals. The old
// parser read "10,000 versions" as 10.000 and rolled 0 -> 10 before snapping
// to "10,000" (a wrong intermediate value). Grouped ints now count 0 -> 10,000
// with commas; decimals keep their original precision.
interface ParsedNumeric {
  num: number;
  prefix: string;
  suffix: string;
  grouped: boolean;
  decimals: number;
}

const parseNumeric = (raw: string): ParsedNumeric | null => {
  const m = /^([^\d-]*)(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)(.*)$/.exec(
    raw.trim(),
  );
  if (!m) return null;
  const numStr = m[2];
  const n = parseFloat(numStr.replace(/,/g, ""));
  if (!isFinite(n)) return null;
  const dot = numStr.indexOf(".");
  return {
    num: n,
    prefix: m[1],
    suffix: m[3],
    grouped: numStr.includes(","),
    decimals: dot >= 0 ? numStr.length - dot - 1 : 0,
  };
};

/** Gate: count-up only when a single metric; must not invent a wrong prefix/bound. */
export const isSimpleSingleNumber = (raw: string): boolean => {
  const trimmed = raw.trim();
  if (!trimmed || RANGE_RE.test(trimmed) || isStaticStat(trimmed)) return false;
  const parsed = parseNumeric(trimmed);
  if (!parsed) return false;
  if (/\d/.test(parsed.suffix)) return false;
  // Bare years (e.g. "2023") are labels, not quantities -- keep static.
  if (!parsed.prefix && !parsed.suffix.trim() && /^(19|20)\d\d$/.test(trimmed)) return false;
  return true;
};

const formatNumeric = (n: number, parsed: ParsedNumeric): string => {
  const opts: Intl.NumberFormatOptions = {
    minimumFractionDigits: parsed.decimals,
    maximumFractionDigits: parsed.decimals,
    useGrouping: parsed.grouped,
  };
  const v = parsed.decimals > 0 ? n : Math.round(n);
  return v.toLocaleString("en-US", opts);
};

/** Count-up at an explicit 0..1 time fraction (used by StatHero). */
export const countUpAt = (value: string, t01: number): string => {
  const trimmed = value.trim();
  if (!isSimpleSingleNumber(trimmed)) return trimmed;
  const parsed = parseNumeric(trimmed)!;
  const t = Math.max(0, Math.min(1, t01));
  if (t >= 1) return trimmed;
  const eased = 1 - Math.pow(1 - t, 3);
  return `${parsed.prefix}${formatNumeric(parsed.num * eased, parsed)}${parsed.suffix}`;
};

/**
 * Whole-value count-up for simple single numbers only (Gavin rule 2).
 * Formats the full number each frame; ranges/multi-number fall through to
 * the trimmed original via isSimpleSingleNumber.
 */
export const countUpDisplay = (value: string, progress: number, localMs?: number): string => {
  const trimmed = value.trim();
  if (!isSimpleSingleNumber(trimmed)) return trimmed;
  const parsed = parseNumeric(trimmed)!;
  // 2026-09-28 item 1: time-based (<= 900 ms) when the caller knows local ms,
  // so a long overlay window can't stretch the count past the spoken value.
  const t =
    localMs !== undefined
      ? Math.max(0, Math.min(1, localMs / 900))
      : interpolate(progress, [0, 0.12], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
  if (t >= 1) return trimmed;
  return `${parsed.prefix}${formatNumeric(parsed.num * t, parsed)}${parsed.suffix}`;
};

export const StatOverlay: React.FC<StatOverlayProps> = ({
  label,
  value,
  subtext,
  progress,
  speakerColor,
  localMs,
}) => {
  // Card scales in from 0.8 -> 1.0 over the first 20% of the overlay window.
  // Slight overshoot at 0.15 gives a spring-like pop without being tied to
  // absolute frame (the overlay window may start mid-composition).
  const scale = interpolate(
    progress,
    [0, 0.15, 0.2],
    [0.8, 1.04, 1.0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const opacity = interpolate(progress, [0, 0.15], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Gavin 2026-09-24: simple single numbers count up; ranges/multi stay static.
  const displayValue = isSimpleSingleNumber(value)
    ? countUpDisplay(value, progress, localMs)
    : value.trim();

  return (
    <div
      style={{
        position: "absolute",
        top: "50%",
        left: "50%",
        transform: `translate(-50%, -50%) scale(${scale})`,
        padding: "48px 72px",
        borderRadius: 24,
        backgroundColor: "rgba(0,0,0,0.6)",
        backdropFilter: "blur(8px)",
        WebkitBackdropFilter: "blur(8px)",
        border: "1px solid rgba(255,255,255,0.15)",
        boxShadow: "0 20px 60px rgba(0,0,0,0.55)",
        fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
        color: "#f5f0e8",
        textAlign: "center",
        opacity,
        minWidth: 420,
        maxWidth: 900,
      }}
    >
      {label ? (
        <div
          style={{
            fontSize: 24,
            fontWeight: 600,
            color: "rgba(245,240,232,0.65)",
            textTransform: "uppercase",
            letterSpacing: 4,
            marginBottom: 12,
          }}
        >
          {label}
        </div>
      ) : null}
      <div
        style={{
          fontSize: 96,
          fontWeight: 900,
          color: speakerColor,
          textShadow: `0 0 30px ${speakerColor}55, 0 4px 12px rgba(0,0,0,0.6)`,
          lineHeight: 1.05,
          letterSpacing: -1,
        }}
      >
        {displayValue}
      </div>
      {subtext ? (
        <div
          style={{
            fontSize: 22,
            fontWeight: 500,
            color: "rgba(245,240,232,0.75)",
            marginTop: 14,
          }}
        >
          {subtext}
        </div>
      ) : null}
    </div>
  );
};