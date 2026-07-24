import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface PullQuoteProps {
  quote: string;
  speaker?: string;
  theme?: "light" | "dark";
}

const BG_DARK = "#1a1a2e";
const BG_LIGHT = "#f5f0e8";
const TEXT_DARK = "#f5f0e8";
const TEXT_LIGHT = "#1a1a2e";
const ACCENT = "#7C3AED";

export const PullQuote: React.FC<PullQuoteProps> = ({
  quote,
  speaker,
  theme = "dark",
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const bg = theme === "light" ? BG_LIGHT : BG_DARK;
  const text = theme === "light" ? TEXT_LIGHT : TEXT_DARK;

  const words = quote.split(/\s+/).filter(Boolean);
  const staggerFrames = 3;
  const revealEnd = Math.floor(durationInFrames * 0.6);

  // optional fade-out in last 10%
  const fadeOutStart = Math.floor(durationInFrames * 0.9);
  const overallOpacity = interpolate(
    frame,
    [fadeOutStart, durationInFrames - 1],
    [1, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(circle at 30% 20%, ${bg} 0%, #0f0f1f 100%)`,
        color: text,
        fontFamily: "Georgia, 'Times New Roman', serif",
        justifyContent: "center",
        alignItems: "center",
        padding: 120,
        opacity: overallOpacity,
      }}
    >
      <div
        style={{
          position: "absolute",
          top: 80,
          left: 100,
          fontSize: 220,
          color: ACCENT,
          lineHeight: 1,
          opacity: 0.6,
          fontFamily: "Georgia, serif",
        }}
      >
        “
      </div>

      <div
        style={{
          fontSize: 56,
          fontStyle: "italic",
          lineHeight: 1.3,
          maxWidth: 1500,
          textAlign: "center",
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          gap: "0 18px",
        }}
      >
        {words.map((w, i) => {
          const startFrame = i * staggerFrames;
          const wp = spring({
            frame: frame - startFrame,
            fps,
            config: { damping: 18, stiffness: 140, mass: 0.5 },
          });
          const opacity = interpolate(wp, [0, 1], [0, 1]);
          const y = interpolate(wp, [0, 1], [12, 0]);
          return (
            <span
              key={i}
              style={{
                opacity,
                transform: `translateY(${y}px)`,
                display: "inline-block",
              }}
            >
              {w}
            </span>
          );
        })}
      </div>

      <div
        style={{
          position: "absolute",
          bottom: 80,
          right: 100,
          fontSize: 220,
          color: ACCENT,
          lineHeight: 1,
          opacity: 0.6,
        }}
      >
        ”
      </div>

      {speaker ? (
        <div
          style={{
            marginTop: 60,
            display: "flex",
            alignItems: "center",
            gap: 16,
            opacity: interpolate(
              frame,
              [revealEnd - 10, revealEnd + 10],
              [0, 1],
              { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
            ),
          }}
        >
          <div
            style={{
              width: 48,
              height: 2,
              backgroundColor: ACCENT,
            }}
          />
          <div
            style={{
              fontFamily: "system-ui, -apple-system, sans-serif",
              fontSize: 24,
              letterSpacing: 4,
              textTransform: "uppercase",
              color: ACCENT,
            }}
          >
            {speaker}
          </div>
        </div>
      ) : null}
    </AbsoluteFill>
  );
};
