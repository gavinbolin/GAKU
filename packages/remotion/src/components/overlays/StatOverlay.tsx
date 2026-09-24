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
 * Stat value reveal -- DEFAULT: static correct text (fade/scale entrance only).
 *
 * Root cause of the Enterprise bug ("5-10%" flashing as "1-10%" ... "4-10%"):
 *   parseNumeric peeled the first number off ranges as num=5 + suffix="-10%",
 *   then lerped 0->5 so mid-frames invented wrong metrics. Commit 7a3c152 only
 *   shortened that window (0.5 -> 0.12); it did not stop the wrong prefixes.
 *
 * Default (a) -- implemented: always render the scripted `value` string from
 *   the first frame. Card opacity + scale provide the entrance motion.
 *
 * Alternatives (for Gavin / product -- not wired as default):
 *   (a) Static snap -- current default. Correct final string immediately;
 *       motion = fade/scale only. Safest for ranges ("5-10%", "3-5x").
 *   (b) Whole-number lerp -- ONLY for simple single numbers ("42%", "$4.8T"):
 *       interpolate the numeric value and re-format prefix+number+suffix each
 *       frame. Never digit-roll; never touch hyphen/en-dash ranges or any
 *       string whose suffix still contains a digit. See isSimpleSingleNumber
 *       + the unused countUpDisplay helper below if you want to opt in.
 *   (c) Progress / fill motion -- keep the number text fixed at the correct
 *       final string; animate a bar, ring, or underline fill underneath
 *       (StatAnimation's ring mode is the precedent).
 */

// Kept for optional alt (b) and for documenting why ranges must stay static.
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

/** Gate for optional alt (b): count-up must not invent a wrong prefix/bound. */
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
 * Optional alt (b): whole-value count-up for simple numbers only.
 * Not used by the default render path -- call from displayValue if product
 * opts into lerp motion later.
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

  // Default (a): scripted value as-is. Do NOT count digits -- ranges and
  // multi-number strings must never pass through wrong intermediate text.
  // To enable alt (b) for simple numbers only: displayValue = countUpDisplay(value, progress);
  const displayValue = value.trim();

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