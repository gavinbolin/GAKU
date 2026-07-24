import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

// FlowDiagram — renders a horizontal step/process flow as a row of boxes
// connected by arrows. Steps fade/slide in sequentially across the visual's
// duration. Use for processes, sequences, and named stages — NOT for
// quantitative data (use GrowthCurve or StatCallout for that).

export interface FlowDiagramStep {
  label: string;
  description?: string;
}

export interface FlowDiagramProps {
  steps: FlowDiagramStep[];
  title?: string;
  activeStep?: number;
  active_step?: number; // accept snake_case from schema/JSON
  theme?: "light" | "dark";
}

const DARK = {
  bg: "#1a1a2e",
  text: "#f5f0e8",
  textMuted: "rgba(245, 240, 232, 0.6)",
  accent: "#7C3AED",
  box: "#252544",
  boxBorder: "rgba(124, 58, 237, 0.5)",
  arrow: "rgba(245, 240, 232, 0.55)",
};

const LIGHT = {
  bg: "#f5f0e8",
  text: "#1a1a2e",
  textMuted: "rgba(26, 26, 46, 0.6)",
  accent: "#7C3AED",
  box: "#ffffff",
  boxBorder: "rgba(124, 58, 237, 0.5)",
  arrow: "rgba(26, 26, 46, 0.45)",
};

export const FlowDiagram: React.FC<FlowDiagramProps> = ({
  steps,
  title,
  activeStep,
  active_step,
  theme = "dark",
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const palette = theme === "light" ? LIGHT : DARK;
  const active = activeStep ?? active_step;

  const titleOpacity = interpolate(frame, [0, 18], [0, 1], {
    extrapolateRight: "clamp",
  });

  const count = Math.max(1, steps.length);
  // Stagger window: reserve the last 20% of the visual for the final step's
  // settle so the diagram is fully present before the visual ends.
  const stagger = durationInFrames * 0.8;
  const perStep = stagger / count;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: palette.bg,
        color: palette.text,
        fontFamily: "Georgia, 'Times New Roman', serif",
        padding: "80px 100px",
        justifyContent: "center",
      }}
    >
      {title ? (
        <div
          style={{
            fontSize: 56,
            fontWeight: 700,
            opacity: titleOpacity,
            marginBottom: 56,
          }}
        >
          {title}
        </div>
      ) : null}

      <div
        style={{
          display: "flex",
          alignItems: "stretch",
          justifyContent: "center",
          gap: 0,
          flexWrap: "wrap",
        }}
      >
        {steps.map((step, i) => {
          const enterFrame = i * perStep;
          const t = interpolate(
            frame,
            [enterFrame, enterFrame + Math.max(8, perStep * 0.5)],
            [0, 1],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
          );
          const opacity = t;
          const translateY = interpolate(t, [0, 1], [24, 0]);
          const isActive = active !== undefined && i === active;

          return (
            <React.Fragment key={i}>
              <div
                style={{
                  flex: "0 0 auto",
                  minWidth: 240,
                  maxWidth: 340,
                  padding: "28px 24px",
                  borderRadius: 14,
                  backgroundColor: palette.box,
                  border: `2px solid ${
                    isActive ? palette.accent : palette.boxBorder
                  }`,
                  boxShadow: isActive
                    ? `0 0 0 4px ${palette.accent}33`
                    : "none",
                  opacity,
                  transform: `translateY(${translateY}px)`,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "flex-start",
                  gap: 8,
                }}
              >
                <div
                  style={{
                    fontSize: 20,
                    letterSpacing: 4,
                    textTransform: "uppercase",
                    color: palette.accent,
                  }}
                >
                  Step {i + 1}
                </div>
                <div
                  style={{
                    fontSize: 32,
                    fontWeight: 700,
                    lineHeight: 1.15,
                  }}
                >
                  {step.label}
                </div>
                {step.description ? (
                  <div
                    style={{
                      fontSize: 20,
                      color: palette.textMuted,
                      lineHeight: 1.3,
                    }}
                  >
                    {step.description}
                  </div>
                ) : null}
              </div>

              {i < steps.length - 1 ? (
                <Arrow
                  color={palette.arrow}
                  opacity={interpolate(
                    frame,
                    [enterFrame + perStep * 0.4, enterFrame + perStep],
                    [0, 1],
                    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
                  )}
                />
              ) : null}
            </React.Fragment>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

const Arrow: React.FC<{ color: string; opacity: number }> = ({
  color,
  opacity,
}) => (
  <div
    style={{
      flex: "0 0 auto",
      width: 56,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      opacity,
    }}
  >
    <svg width="48" height="24" viewBox="0 0 48 24">
      <path
        d="M2 12 L40 12 M30 4 L40 12 L30 20"
        stroke={color}
        strokeWidth={3}
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  </div>
);
