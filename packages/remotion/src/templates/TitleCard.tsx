import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface TitleCardProps {
  title: string;
  subtitle?: string;
}

const BG = "#1a1a2e";
const TEXT = "#f5f0e8";
const ACCENT = "#7C3AED";

// Match SegmentCard's transition window so adjacent cards xfade cleanly.
const TRANSITION_FRAMES = 15;

export const TitleCard: React.FC<TitleCardProps> = ({ title, subtitle }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  // Card-level intro/outro opacity that wraps the whole composition so the
  // ffmpeg xfade overlap blends into the next clip.
  const cardIntroOpacity = interpolate(
    frame,
    [0, TRANSITION_FRAMES],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const cardOutroProgress = interpolate(
    frame,
    [durationInFrames - TRANSITION_FRAMES, durationInFrames],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const cardOpacity = cardIntroOpacity * (1 - cardOutroProgress);

  const subtitleOpacity = interpolate(frame, [6, 24], [0, 1], {
    extrapolateRight: "clamp",
    extrapolateLeft: "clamp",
  });

  const titleY = spring({
    frame: frame - 12,
    fps,
    config: { damping: 14, stiffness: 110, mass: 0.6 },
  });
  const titleTranslate = interpolate(titleY, [0, 1], [40, 0]);
  const titleOpacity = interpolate(titleY, [0, 1], [0, 1]);

  const underlineWidth = interpolate(frame, [28, 50], [0, 1], {
    extrapolateRight: "clamp",
    extrapolateLeft: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: BG,
        fontFamily: "Georgia, 'Times New Roman', serif",
        color: TEXT,
        padding: "0 120px",
        justifyContent: "center",
        opacity: cardOpacity,
      }}
    >
      {subtitle ? (
        <div
          style={{
            fontSize: 36,
            opacity: subtitleOpacity,
            letterSpacing: 6,
            textTransform: "uppercase",
            color: ACCENT,
            marginBottom: 24,
          }}
        >
          {subtitle}
        </div>
      ) : null}

      <div
        style={{
          fontSize: 110,
          fontWeight: 700,
          lineHeight: 1.05,
          opacity: titleOpacity,
          transform: `translateY(${titleTranslate}px)`,
        }}
      >
        {title}
      </div>

      <div
        style={{
          marginTop: 40,
          height: 6,
          width: `${underlineWidth * 220}px`,
          backgroundColor: ACCENT,
          borderRadius: 4,
        }}
      />
    </AbsoluteFill>
  );
};
