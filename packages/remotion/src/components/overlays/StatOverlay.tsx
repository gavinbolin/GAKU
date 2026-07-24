import React from "react";
import { interpolate } from "remotion";

export interface StatOverlayProps {
  label: string;
  value: string;
  subtext?: string;
  progress: number;
  speakerColor: string;
}

// Try to parse a numeric prefix out of the value string ("$4.8T", "1.2 billion").
// Returns null if no numeric portion was found.
const parseNumeric = (raw: string): { num: number; prefix: string; suffix: string } | null => {
  const m = /^([^\d-]*)(-?\d+(?:[.,]\d+)?)(.*)$/.exec(raw.trim());
  if (!m) return null;
  const n = parseFloat(m[2].replace(/,/g, "."));
  if (!isFinite(n)) return null;
  return { num: n, prefix: m[1], suffix: m[3] };
};

const formatNumeric = (n: number, original: number): string => {
  // Preserve roughly the original precision (no trailing decimals on integers).
  if (Math.floor(original) === original) return Math.round(n).toString();
  return n.toFixed(1);
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

  // If the value parses as a number, count up to it over the first 50% of
  // progress. Otherwise just render the string.
  const parsed = parseNumeric(value);
  let displayValue: string;
  if (parsed) {
    const t = interpolate(progress, [0, 0.5], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
    const animated = parsed.num * t;
    displayValue = `${parsed.prefix}${formatNumeric(animated, parsed.num)}${parsed.suffix}`;
  } else {
    displayValue = value;
  }

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
