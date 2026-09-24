import React from "react";
import { interpolate } from "remotion";

export interface StatOverlayProps {
  label: string;
  value: string;
  subtext?: string;
  progress: number;
  speakerColor: string;
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
const RANGE_RE = /\d\s*[-–—]\s*\d/;

const parseNumeric = (
  raw: string,
): { num: number; prefix: string; suffix: string } | null => {
  const m = /^([^\d-]*)(-?\d+(?:[.,]\d+)?)(.*)$/.exec(raw.trim());
  if (!m) return null;
  const n = parseFloat(m[2].replace(/,/g, "."));
  if (!isFinite(n)) return null;
  return { num: n, prefix: m[1], suffix: m[3] };
};

/** Gate: count-up only when a single metric; must not invent a wrong prefix/bound. */
export const isSimpleSingleNumber = (raw: string): boolean => {
  const trimmed = raw.trim();
  if (!trimmed || RANGE_RE.test(trimmed)) return false;
  const parsed = parseNumeric(trimmed);
  if (!parsed) return false;
  if (/\d/.test(parsed.suffix)) return false;
  return true;
};

const formatNumeric = (n: number, original: number): string => {
  if (Math.floor(original) === original) return Math.round(n).toString();
  return n.toFixed(1);
};

/**
 * Whole-value count-up for simple single numbers only (Gavin rule 2).
 * Formats the full number each frame; ranges/multi-number fall through to
 * the trimmed original via isSimpleSingleNumber.
 */
export const countUpDisplay = (value: string, progress: number): string => {
  const trimmed = value.trim();
  if (!isSimpleSingleNumber(trimmed)) return trimmed;
  const parsed = parseNumeric(trimmed)!;
  const t = interpolate(progress, [0, 0.12], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  if (t >= 1) return trimmed;
  return `${parsed.prefix}${formatNumeric(parsed.num * t, parsed.num)}${parsed.suffix}`;
};

export const StatOverlay: React.FC<StatOverlayProps> = ({
  label,
  value,
  subtext,
  progress,
  speakerColor,
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
    ? countUpDisplay(value, progress)
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