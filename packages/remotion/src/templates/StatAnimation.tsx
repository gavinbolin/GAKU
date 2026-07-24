import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface StatAnimationProps {
  value: number;
  unit?: string;
  prefix?: string;
  caption: string;
  source?: string;
  animate_type?: "counter" | "ring";
}

const BG = "#1a1a2e";
const TEXT = "#f5f0e8";
const ACCENT = "#7C3AED";
const MUTED = "rgba(245, 240, 232, 0.55)";
const RING_BG = "rgba(245, 240, 232, 0.12)";

export const StatAnimation: React.FC<StatAnimationProps> = ({
  value,
  unit,
  prefix,
  caption,
  source,
  animate_type = "counter",
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const countEnd = Math.floor(durationInFrames * 0.6);
  const animated = interpolate(frame, [0, countEnd], [0, value], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const display = formatNumber(animated, value);

  const captionStart = Math.floor(countEnd * 0.5);
  const captionOpacity = interpolate(
    frame,
    [captionStart, captionStart + 20],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const captionY = interpolate(
    frame,
    [captionStart, captionStart + 20],
    [16, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  if (animate_type === "ring") {
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
        <Ring
          percent={Math.max(0, Math.min(100, animated))}
          targetPercent={value}
          frame={frame}
          durationInFrames={durationInFrames}
          label={null}
        />

        <div
          style={{
            marginTop: 48,
            fontSize: 36,
            maxWidth: 1200,
            textAlign: "center",
            opacity: captionOpacity,
            transform: `translateY(${captionY}px)`,
          }}
        >
          {caption}
        </div>

        {source ? (
          <div
            style={{
              marginTop: 18,
              fontSize: 18,
              color: MUTED,
              letterSpacing: 2,
              textTransform: "uppercase",
              fontFamily: "system-ui, -apple-system, sans-serif",
              opacity: captionOpacity,
            }}
          >
            {source}
          </div>
        ) : null}
      </AbsoluteFill>
    );
  }

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
      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        {prefix ? (
          <div style={{ fontSize: 80, opacity: 0.85, color: ACCENT }}>
            {prefix}
          </div>
        ) : null}
        <div
          style={{
            fontSize: 240,
            fontWeight: 700,
            color: ACCENT,
            lineHeight: 1,
          }}
        >
          {display}
        </div>
        {unit ? (
          <div style={{ fontSize: 80, opacity: 0.85 }}>{unit}</div>
        ) : null}
      </div>

      <div
        style={{
          marginTop: 28,
          fontSize: 40,
          maxWidth: 1200,
          textAlign: "center",
          opacity: captionOpacity,
          transform: `translateY(${captionY}px)`,
        }}
      >
        {caption}
      </div>

      {source ? (
        <div
          style={{
            marginTop: 18,
            fontSize: 18,
            color: MUTED,
            letterSpacing: 2,
            textTransform: "uppercase",
            fontFamily: "system-ui, -apple-system, sans-serif",
            opacity: captionOpacity,
          }}
        >
          {source}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

const Ring: React.FC<{
  percent: number;
  targetPercent: number;
  frame: number;
  durationInFrames: number;
  label: string | null;
}> = ({ percent, targetPercent, frame, durationInFrames }) => {
  const size = 600;
  const stroke = 36;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;

  const fillEnd = Math.floor(durationInFrames * 0.7);
  const fillPct = interpolate(frame, [0, fillEnd], [0, targetPercent], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const dash = (fillPct / 100) * c;
  const displayPct = Math.round(percent);

  return (
    <div style={{ position: "relative", width: size, height: size }}>
      <svg width={size} height={size}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={RING_BG}
          strokeWidth={stroke}
          fill="none"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={ACCENT}
          strokeWidth={stroke}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${c}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 160,
          fontWeight: 700,
          color: ACCENT,
          fontFamily: "Georgia, serif",
        }}
      >
        {displayPct}
        <span style={{ fontSize: 80, marginLeft: 4 }}>%</span>
      </div>
    </div>
  );
};

function formatNumber(animated: number, target: number): string {
  const targetStr = String(target);
  const dot = targetStr.indexOf(".");
  if (dot === -1) return Math.round(animated).toLocaleString();
  const decimals = targetStr.length - dot - 1;
  return animated.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}
