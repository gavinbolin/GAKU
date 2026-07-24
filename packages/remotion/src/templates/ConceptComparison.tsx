import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface ConceptComparisonProps {
  left: { label: string; points: string[] };
  right: { label: string; points: string[] };
  title?: string;
  left_color?: string;
  right_color?: string;
}

const BG = "#1a1a2e";
const TEXT = "#f5f0e8";
const ACCENT = "#7C3AED";
const BLUE = "#0EA5E9";

export const ConceptComparison: React.FC<ConceptComparisonProps> = ({
  left,
  right,
  title,
  left_color = ACCENT,
  right_color = BLUE,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleOpacity = interpolate(frame, [0, 14], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const headerStart = 14;
  const headerSpring = spring({
    frame: frame - headerStart,
    fps,
    config: { damping: 18, stiffness: 110, mass: 0.6 },
  });
  const headerOpacity = interpolate(headerSpring, [0, 1], [0, 1]);
  const headerY = interpolate(headerSpring, [0, 1], [20, 0]);

  const dividerProgress = interpolate(frame, [headerStart + 6, headerStart + 30], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const pointStart = headerStart + 24;
  const pointStaggerFrames = 20;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: BG,
        color: TEXT,
        fontFamily: "Georgia, 'Times New Roman', serif",
        padding: "80px 100px",
      }}
    >
      {title ? (
        <div
          style={{
            fontSize: 48,
            fontWeight: 700,
            textAlign: "center",
            opacity: titleOpacity,
            marginBottom: 40,
          }}
        >
          {title}
        </div>
      ) : null}

      <div
        style={{
          flex: 1,
          display: "grid",
          gridTemplateColumns: "1fr 80px 1fr",
          gap: 0,
          alignItems: "stretch",
        }}
      >
        <Column
          label={left.label}
          color={left_color}
          points={left.points}
          align="right"
          slideFrom={-60}
          headerOpacity={headerOpacity}
          headerY={headerY}
          frame={frame}
          fps={fps}
          pointStart={pointStart}
          pointStaggerFrames={pointStaggerFrames}
          offsetIndex={0}
        />

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            position: "relative",
          }}
        >
          <div
            style={{
              width: 3,
              height: `${dividerProgress * 100}%`,
              backgroundColor: "rgba(245, 240, 232, 0.25)",
              position: "absolute",
              top: 0,
            }}
          />
          <div
            style={{
              fontSize: 56,
              fontWeight: 700,
              color: TEXT,
              backgroundColor: BG,
              padding: "12px 0",
              opacity: titleOpacity,
              fontFamily: "Georgia, serif",
              letterSpacing: 4,
            }}
          >
            VS
          </div>
        </div>

        <Column
          label={right.label}
          color={right_color}
          points={right.points}
          align="left"
          slideFrom={60}
          headerOpacity={headerOpacity}
          headerY={headerY}
          frame={frame}
          fps={fps}
          pointStart={pointStart}
          pointStaggerFrames={pointStaggerFrames}
          offsetIndex={1}
        />
      </div>
    </AbsoluteFill>
  );
};

const Column: React.FC<{
  label: string;
  color: string;
  points: string[];
  align: "left" | "right";
  slideFrom: number;
  headerOpacity: number;
  headerY: number;
  frame: number;
  fps: number;
  pointStart: number;
  pointStaggerFrames: number;
  offsetIndex: number;
}> = ({
  label,
  color,
  points,
  align,
  slideFrom,
  headerOpacity,
  headerY,
  frame,
  fps,
  pointStart,
  pointStaggerFrames,
  offsetIndex,
}) => {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: align === "right" ? "flex-end" : "flex-start",
        padding: align === "right" ? "0 60px 0 0" : "0 0 0 60px",
      }}
    >
      <div
        style={{
          fontSize: 56,
          fontWeight: 700,
          color,
          opacity: headerOpacity,
          transform: `translateY(${headerY}px)`,
          marginBottom: 40,
          textAlign: align,
        }}
      >
        {label}
      </div>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 24,
          width: "100%",
          alignItems: align === "right" ? "flex-end" : "flex-start",
        }}
      >
        {points.map((p, i) => {
          // alternate left/right so they staircase in
          const idx = i * 2 + offsetIndex;
          const start = pointStart + idx * (pointStaggerFrames / 2);
          const sp = spring({
            frame: frame - start,
            fps,
            config: { damping: 18, stiffness: 120, mass: 0.6 },
          });
          const opacity = interpolate(sp, [0, 1], [0, 1]);
          const x = interpolate(sp, [0, 1], [slideFrom, 0]);
          return (
            <div
              key={i}
              style={{
                opacity,
                transform: `translateX(${x}px)`,
                display: "flex",
                alignItems: "flex-start",
                gap: 14,
                flexDirection: align === "right" ? "row-reverse" : "row",
                fontSize: 28,
                lineHeight: 1.4,
                maxWidth: 700,
                textAlign: align,
              }}
            >
              <span
                style={{
                  color,
                  fontSize: 32,
                  lineHeight: 1.1,
                  marginTop: -2,
                }}
              >
                •
              </span>
              <span>{p}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
};
