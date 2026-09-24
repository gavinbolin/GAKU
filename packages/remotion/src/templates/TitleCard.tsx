import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { PodcastBackground } from "./PodcastBackground";

export interface TitleCardProps {
  title: string;
  subtitle?: string;
}

const TEXT = "#f5f0e8";
const ACCENT = "#7C3AED";
const ACCENT_BRIGHT = "#FFFFFF";

const TRANSITION_FRAMES = 15;

export const TitleCard: React.FC<TitleCardProps> = ({ title, subtitle }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

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

  const brandSpring = spring({
    frame,
    fps,
    config: { damping: 16, stiffness: 120, mass: 0.55 },
  });
  const brandOpacity = interpolate(brandSpring, [0, 1], [0, 1]);
  const brandY = interpolate(brandSpring, [0, 1], [-16, 0]);

  const titleSpring = spring({
    frame: frame - 10,
    fps,
    config: { damping: 14, stiffness: 110, mass: 0.65 },
  });
  const titleTranslate = interpolate(titleSpring, [0, 1], [72, 0]);
  const titleOpacity = interpolate(titleSpring, [0, 1], [0, 1]);
  const titleScale = interpolate(titleSpring, [0, 1], [0.94, 1]);

  const underlineWidth = interpolate(frame, [28, 52], [0, 1], {
    extrapolateRight: "clamp",
    extrapolateLeft: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        fontFamily: "Georgia, 'Times New Roman', serif",
        color: TEXT,
        overflow: "hidden",
      }}
    >
      <PodcastBackground topic={title} subtitle="" variant="backdrop" />

      <AbsoluteFill
        style={{
          padding: "0 120px",
          justifyContent: "center",
          opacity: cardOpacity,
        }}
      >
        {subtitle ? (
          <div
            style={{
              fontSize: 38,
              letterSpacing: 10,
              textTransform: "uppercase",
              color: ACCENT_BRIGHT,
              marginBottom: 28,
              fontWeight: 700,
              opacity: brandOpacity,
              transform: `translateY(${brandY}px)`,
              textShadow:
                "0 0 24px rgba(233,213,255,1), 0 0 52px rgba(167,139,250,0.95), 0 2px 8px rgba(0,0,0,0.55)",
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
            transform: `translateY(${titleTranslate}px) scale(${titleScale})`,
            transformOrigin: "left center",
            textShadow: "0 10px 40px rgba(0,0,0,0.45)",
          }}
        >
          {title}
        </div>

        <div
          style={{
            marginTop: 40,
            height: 6,
            width: `${underlineWidth * 280}px`,
            backgroundColor: ACCENT,
            borderRadius: 4,
            boxShadow: `0 0 18px ${ACCENT}`,
          }}
        />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
