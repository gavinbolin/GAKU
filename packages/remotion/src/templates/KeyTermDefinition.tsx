import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
} from "remotion";

export interface KeyTermDefinitionProps {
  term: string;
  definition: string;
  etymology?: string;
  category?: string;
}

const BG = "#1a1a2e";
const TEXT = "#f5f0e8";
const ACCENT = "#7C3AED";
const MUTED = "rgba(245, 240, 232, 0.6)";

export const KeyTermDefinition: React.FC<KeyTermDefinitionProps> = ({
  term,
  definition,
  etymology,
  category,
}) => {
  const frame = useCurrentFrame();

  // typewriter reveal for the term over first 30 frames
  const revealFrames = 30;
  const charCount = Math.max(
    0,
    Math.min(
      term.length,
      Math.floor(interpolate(frame, [0, revealFrames], [0, term.length])),
    ),
  );
  const visibleTerm = term.slice(0, charCount);
  const caretBlink = Math.floor(frame / 10) % 2 === 0;

  const defOpacity = interpolate(
    frame,
    [revealFrames + 4, revealFrames + 28],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const defY = interpolate(
    frame,
    [revealFrames + 4, revealFrames + 28],
    [12, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const etymOpacity = interpolate(
    frame,
    [revealFrames + 30, revealFrames + 56],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  return (
    <AbsoluteFill
      style={{
        backgroundColor: BG,
        color: TEXT,
        fontFamily: "Georgia, 'Times New Roman', serif",
        padding: "120px 140px",
        justifyContent: "center",
      }}
    >
      {category ? (
        <div
          style={{
            position: "absolute",
            top: 80,
            right: 100,
            padding: "10px 22px",
            border: `2px solid ${ACCENT}`,
            borderRadius: 999,
            color: ACCENT,
            fontFamily: "system-ui, -apple-system, sans-serif",
            fontSize: 22,
            letterSpacing: 3,
            textTransform: "uppercase",
          }}
        >
          {category}
        </div>
      ) : null}

      <div
        style={{
          fontSize: 120,
          fontWeight: 700,
          color: ACCENT,
          lineHeight: 1.05,
          letterSpacing: -1,
        }}
      >
        {visibleTerm}
        {charCount < term.length && caretBlink ? (
          <span style={{ marginLeft: 6, opacity: 0.7 }}>|</span>
        ) : null}
      </div>

      <div
        style={{
          marginTop: 28,
          height: 4,
          width: 220,
          backgroundColor: ACCENT,
          borderRadius: 2,
        }}
      />

      <div
        style={{
          marginTop: 48,
          fontSize: 42,
          lineHeight: 1.4,
          maxWidth: 1500,
          opacity: defOpacity,
          transform: `translateY(${defY}px)`,
        }}
      >
        {definition}
      </div>

      {etymology ? (
        <div
          style={{
            marginTop: 36,
            fontSize: 22,
            fontStyle: "italic",
            color: MUTED,
            maxWidth: 1300,
            opacity: etymOpacity,
          }}
        >
          {etymology}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};
