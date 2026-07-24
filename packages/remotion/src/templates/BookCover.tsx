import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface BookCoverProps {
  title: string;
  author: string;
}

const BG = "#1a1a2e";
const TEXT = "#f5f0e8";
const ACCENT = "#7C3AED";

export const BookCover: React.FC<BookCoverProps> = ({ title, author }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const authorOpacity = interpolate(frame, [4, 22], [0, 1], {
    extrapolateRight: "clamp",
    extrapolateLeft: "clamp",
  });

  const titleSpring = spring({
    frame: frame - 18,
    fps,
    config: { damping: 14, stiffness: 110, mass: 0.6 },
  });
  const titleTranslate = interpolate(titleSpring, [0, 1], [40, 0]);
  const titleOpacity = interpolate(titleSpring, [0, 1], [0, 1]);

  const ruleWidth = interpolate(frame, [34, 64], [0, 1], {
    extrapolateRight: "clamp",
    extrapolateLeft: "clamp",
  });

  // Soft pulsing glow on the accent color: sin wave -> [0,1] opacity range.
  const pulse = (Math.sin((frame / fps) * Math.PI * 1.2) + 1) / 2;
  const glowOpacity = interpolate(pulse, [0, 1], [0.25, 0.7]);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: BG,
        fontFamily: "Georgia, 'Times New Roman', serif",
        color: TEXT,
        padding: "0 160px",
        justifyContent: "center",
        alignItems: "center",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          width: 900,
          height: 900,
          marginTop: -450,
          marginLeft: -450,
          borderRadius: "50%",
          background: `radial-gradient(circle, ${ACCENT}33 0%, transparent 65%)`,
          opacity: glowOpacity,
          filter: "blur(40px)",
          pointerEvents: "none",
        }}
      />

      <div
        style={{
          fontSize: 32,
          letterSpacing: 8,
          textTransform: "uppercase",
          color: ACCENT,
          opacity: authorOpacity,
          marginBottom: 60,
          position: "relative",
          zIndex: 1,
        }}
      >
        {author}
      </div>

      <div
        style={{
          fontSize: 120,
          fontWeight: 700,
          lineHeight: 1.05,
          textAlign: "center",
          maxWidth: 1500,
          opacity: titleOpacity,
          transform: `translateY(${titleTranslate}px)`,
          position: "relative",
          zIndex: 1,
        }}
      >
        {title}
      </div>

      <div
        style={{
          marginTop: 48,
          height: 4,
          width: `${ruleWidth * 380}px`,
          backgroundColor: ACCENT,
          borderRadius: 4,
          position: "relative",
          zIndex: 1,
        }}
      />
    </AbsoluteFill>
  );
};
