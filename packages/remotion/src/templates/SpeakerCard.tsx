import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface SpeakerCardProps {
  host_name: string;
  host_role: "primary" | "secondary";
  color: string;
  show_waveform?: boolean;
}

const TEXT = "#f5f0e8";

export const SpeakerCard: React.FC<SpeakerCardProps> = ({
  host_name,
  host_role,
  color,
  show_waveform = true,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const slide = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 110, mass: 0.7 },
  });
  const translateX = interpolate(slide, [0, 1], [-120, 0]);
  const opacity = interpolate(slide, [0, 1], [0, 1]);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "transparent",
        justifyContent: "flex-end",
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          width: "100%",
          height: 100,
          backgroundColor: "rgba(0,0,0,0.65)",
          backdropFilter: "blur(6px)",
          display: "flex",
          alignItems: "center",
          padding: "0 48px",
          borderTop: `2px solid ${color}`,
          opacity,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 24,
            transform: `translateX(${translateX}px)`,
          }}
        >
          <div
            style={{
              width: 8,
              height: 56,
              backgroundColor: color,
              borderRadius: 4,
            }}
          />
          <div style={{ display: "flex", flexDirection: "column" }}>
            <div
              style={{
                fontFamily: "Georgia, 'Times New Roman', serif",
                fontSize: 32,
                fontWeight: 700,
                color: TEXT,
                lineHeight: 1.1,
              }}
            >
              {host_name}
            </div>
            <div
              style={{
                fontFamily: "system-ui, -apple-system, sans-serif",
                fontSize: 14,
                letterSpacing: 3,
                textTransform: "uppercase",
                color,
                marginTop: 2,
              }}
            >
              {host_role === "primary" ? "Host" : "Co-Host"}
            </div>
          </div>

          {show_waveform ? (
            <Waveform color={color} frame={frame} />
          ) : null}
        </div>
      </div>
    </AbsoluteFill>
  );
};

const Waveform: React.FC<{ color: string; frame: number }> = ({
  color,
  frame,
}) => {
  const bars = 5;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        marginLeft: 32,
        height: 48,
      }}
    >
      {Array.from({ length: bars }).map((_, i) => {
        // offset sin waves; speeds vary slightly per bar to avoid sync feel.
        const phase = i * 0.9;
        const speed = 0.22 + i * 0.04;
        const amp = 0.5 + 0.5 * Math.sin(frame * speed + phase);
        const h = 8 + amp * 32;
        return (
          <div
            key={i}
            style={{
              width: 5,
              height: h,
              backgroundColor: color,
              borderRadius: 2,
              opacity: 0.85,
            }}
          />
        );
      })}
    </div>
  );
};
