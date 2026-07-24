import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface PercentageRingProps {
  percentage: number;
  label: string;
  color?: string;
  show_remaining?: boolean;
}

const BG = "#1a1a2e";
const TEXT = "#f5f0e8";
const ACCENT = "#7C3AED";
const REMAINING = "rgba(245, 240, 232, 0.12)";

export const PercentageRing: React.FC<PercentageRingProps> = ({
  percentage,
  label,
  color = ACCENT,
  show_remaining = true,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const fillEnd = Math.floor(durationInFrames * 0.7);
  const target = Math.max(0, Math.min(100, percentage));
  const animated = interpolate(frame, [0, fillEnd], [0, target], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const displayPct = Math.round(animated);

  // 60% of frame height diameter (1080 * 0.6 = 648)
  const size = 648;
  const stroke = 44;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const dash = (animated / 100) * c;

  const labelOpacity = interpolate(frame, [20, 50], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const labelY = interpolate(frame, [20, 50], [20, 0], {
    extrapolateLeft: "clamp",
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
        padding: 60,
      }}
    >
      <div style={{ position: "relative", width: size, height: size }}>
        <svg width={size} height={size}>
          {show_remaining ? (
            <circle
              cx={size / 2}
              cy={size / 2}
              r={r}
              stroke={REMAINING}
              strokeWidth={stroke}
              fill="none"
            />
          ) : null}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            stroke={color}
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
          }}
        >
          <span style={{ fontSize: 200, fontWeight: 700, color }}>
            {displayPct}
          </span>
          <span style={{ fontSize: 100, color, marginLeft: 6 }}>%</span>
        </div>
      </div>

      <div
        style={{
          marginTop: 32,
          fontSize: 38,
          maxWidth: 1300,
          textAlign: "center",
          opacity: labelOpacity,
          transform: `translateY(${labelY}px)`,
          lineHeight: 1.3,
        }}
      >
        {label}
      </div>
    </AbsoluteFill>
  );
};
