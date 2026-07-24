import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface ChapterCardProps {
  chapter_num: number;
  chapter_title: string;
}

const BG = "#1a1a2e";
const TEXT = "#f5f0e8";
const ACCENT = "#7C3AED";

export const ChapterCard: React.FC<ChapterCardProps> = ({
  chapter_num,
  chapter_title,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const labelSpring = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 120, mass: 0.6 },
  });
  const labelX = interpolate(labelSpring, [0, 1], [-80, 0]);
  const labelOpacity = interpolate(labelSpring, [0, 1], [0, 1]);

  const titleSpring = spring({
    frame: frame - 10,
    fps,
    config: { damping: 16, stiffness: 100, mass: 0.7 },
  });
  const titleY = interpolate(titleSpring, [0, 1], [24, 0]);
  const titleOpacity = interpolate(titleSpring, [0, 1], [0, 1]);

  // Progress sweep across the full bottom over the card's full duration.
  const sweep = interpolate(frame, [0, durationInFrames - 1], [0, 1], {
    extrapolateRight: "clamp",
    extrapolateLeft: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: BG,
        fontFamily: "Georgia, 'Times New Roman', serif",
        color: TEXT,
        padding: "0 160px",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          fontSize: 36,
          letterSpacing: 10,
          textTransform: "uppercase",
          color: ACCENT,
          fontVariant: "small-caps",
          opacity: labelOpacity,
          transform: `translateX(${labelX}px)`,
          marginBottom: 28,
        }}
      >
        Chapter {chapter_num}
      </div>

      <div
        style={{
          fontSize: 96,
          fontWeight: 700,
          lineHeight: 1.05,
          maxWidth: 1500,
          opacity: titleOpacity,
          transform: `translateY(${titleY}px)`,
        }}
      >
        {chapter_title}
      </div>

      <div
        style={{
          position: "absolute",
          left: 0,
          bottom: 0,
          height: 4,
          width: `${sweep * 100}%`,
          backgroundColor: ACCENT,
        }}
      />
    </AbsoluteFill>
  );
};
