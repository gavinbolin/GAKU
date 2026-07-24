import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface StatCalloutProps {
  value: number;
  unit?: string;
  caption: string;
}

const BG = "#1a1a2e";
const TEXT = "#f5f0e8";
const ACCENT = "#7C3AED";

export const StatCallout: React.FC<StatCalloutProps> = ({
  value,
  unit,
  caption,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const countProgress = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 75, mass: 1 },
  });
  const animated = value * countProgress;
  const display = formatNumber(animated, value);

  const captionOpacity = interpolate(frame, [16, 36], [0, 1], {
    extrapolateRight: "clamp",
  });
  const captionTranslate = interpolate(frame, [16, 36], [16, 0], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: BG,
        color: TEXT,
        fontFamily: "Georgia, 'Times New Roman', serif",
        justifyContent: "center",
        alignItems: "center",
        padding: 80,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 16,
        }}
      >
        <div style={{ fontSize: 280, fontWeight: 700, color: ACCENT }}>
          {display}
        </div>
        {unit ? (
          <div style={{ fontSize: 80, opacity: 0.85 }}>{unit}</div>
        ) : null}
      </div>

      <div
        style={{
          marginTop: 24,
          fontSize: 40,
          maxWidth: 1100,
          textAlign: "center",
          opacity: captionOpacity,
          transform: `translateY(${captionTranslate}px)`,
        }}
      >
        {caption}
      </div>
    </AbsoluteFill>
  );
};

function formatNumber(animated: number, target: number): string {
  // Match the precision of the target number — integers stay integer,
  // decimals respect the original count of fractional digits.
  const targetStr = String(target);
  const dot = targetStr.indexOf(".");
  if (dot === -1) return Math.round(animated).toLocaleString();
  const decimals = targetStr.length - dot - 1;
  return animated.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}
