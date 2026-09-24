import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

/**
 * PodcastBackground -- the looped base layer for a podcast video.
 *
 * variant "full": topic + subtitle + waveform (standalone looping clip).
 * variant "backdrop": living gradient / orbs / particles only, for use
 * behind TitleCard and SegmentCard so those cards are never a still.
 *
 * Motion is driven by phaseCycle = (frame / duration) * 2pi so a looping
 * composition seams cleanly (sin(n * phaseCycle) matches at the wrap).
 */

export interface PodcastBackgroundProps {
  topic: string;
  subtitle?: string;
  variant?: "full" | "backdrop";
}

const TEXT = "#f5f0e8";
const ACCENT_BRIGHT = "#E9D5FF";
const WAVE_COLOR = "rgba(167, 139, 250, 0.55)";

const BAR_COUNT = 60;
const BAR_WIDTH = 12;
const BAR_GAP = 18;
const PARTICLE_COUNT = 22;

export const PodcastBackground: React.FC<PodcastBackgroundProps> = ({
  topic,
  subtitle = "PROJECT GAKU",
  variant = "full",
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const dur = Math.max(1, durationInFrames);
  const phaseCycle = (frame / dur) * Math.PI * 2;
  const isBackdrop = variant === "backdrop";

  const bars = React.useMemo(
    () => Array.from({ length: BAR_COUNT }, (_, i) => i),
    [],
  );
  const particles = React.useMemo(
    () =>
      Array.from({ length: PARTICLE_COUNT }, (_, i) => ({
        x: (i * 137 + 19) % 100,
        y: (i * 89 + 7) % 100,
        size: 3 + (i % 5),
        depth: 0.35 + (i % 4) * 0.15,
      })),
    [],
  );

  // Slow 360-degree gradient rotation over the clip; last-frame wrap is ~1deg.
  const gradAngle = (frame / dur) * 360;
  const panX = Math.sin(phaseCycle) * 2.4;
  const panY = Math.cos(phaseCycle * 2) * 1.6;

  const orbA = {
    x: 32 + Math.sin(phaseCycle) * 10,
    y: 28 + Math.cos(phaseCycle * 2) * 8,
  };
  const orbB = {
    x: 72 + Math.cos(phaseCycle) * 9,
    y: 62 + Math.sin(phaseCycle * 2) * 7,
  };

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#09091a",
        overflow: "hidden",
        fontFamily: "Georgia, 'Times New Roman', serif",
        color: TEXT,
      }}
    >
      {/* Rotating gradient wash. */}
      <AbsoluteFill
        style={{
          background: `linear-gradient(${gradAngle}deg, #0b0b1c 0%, #1a0b3d 38%, #2a1570 68%, #0f2a5e 100%)`,
          transform: `translate(${panX}%, ${panY}%) scale(1.12)`,
          transformOrigin: "center",
        }}
      />

      {/* Drifting color orbs -- reads as live even at a glance. */}
      <div
        style={{
          position: "absolute",
          width: 920,
          height: 920,
          borderRadius: "50%",
          left: `${orbA.x}%`,
          top: `${orbA.y}%`,
          transform: "translate(-50%, -50%)",
          background:
            "radial-gradient(circle, rgba(124,58,237,0.38) 0%, rgba(124,58,237,0) 70%)",
          pointerEvents: "none",
        }}
      />
      <div
        style={{
          position: "absolute",
          width: 780,
          height: 780,
          borderRadius: "50%",
          left: `${orbB.x}%`,
          top: `${orbB.y}%`,
          transform: "translate(-50%, -50%)",
          background:
            "radial-gradient(circle, rgba(14,165,233,0.28) 0%, rgba(14,165,233,0) 70%)",
          pointerEvents: "none",
        }}
      />

      {/* Soft particles. */}
      {particles.map((p, i) => {
        const dx = Math.sin(phaseCycle + i * 0.5) * 18;
        const dy = Math.cos(phaseCycle * 2 + i * 0.35) * 14;
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: `calc(${p.x}% + ${dx}px)`,
              top: `calc(${p.y}% + ${dy}px)`,
              width: p.size,
              height: p.size,
              borderRadius: "50%",
              backgroundColor: `rgba(233, 213, 255, ${p.depth})`,
              boxShadow: `0 0 ${p.size * 3}px rgba(196, 181, 253, 0.45)`,
              pointerEvents: "none",
            }}
          />
        );
      })}

      {isBackdrop ? null : (
        <AbsoluteFill
          style={{
            justifyContent: "center",
            alignItems: "center",
            padding: "0 120px",
            textAlign: "center",
          }}
        >
          {subtitle ? (
            <div
              style={{
                fontSize: 30,
                letterSpacing: 8,
                textTransform: "uppercase",
                color: ACCENT_BRIGHT,
                marginBottom: 36,
                textShadow:
                  "0 0 18px rgba(167,139,250,0.85), 0 0 36px rgba(124,58,237,0.5)",
                fontWeight: 700,
              }}
            >
              {subtitle}
            </div>
          ) : null}

          <div
            style={{
              fontSize: 84,
              fontWeight: 700,
              lineHeight: 1.1,
              maxWidth: 1500,
              textShadow: "0 8px 32px rgba(0,0,0,0.45)",
            }}
          >
            {topic}
          </div>
        </AbsoluteFill>
      )}

      {isBackdrop ? null : (
        <div
          style={{
            position: "absolute",
            bottom: 80,
            left: 0,
            right: 0,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            gap: BAR_GAP - BAR_WIDTH,
            height: 200,
          }}
        >
          {bars.map((i) => {
            const phase = i * 0.32;
            const a = Math.sin(phaseCycle * 2 + phase);
            const b = Math.sin(phaseCycle * 3 + phase * 1.7);
            const c = Math.sin(phaseCycle * 1 + phase * 0.4);
            const raw = a * 0.4 + b * 0.3 + c * 0.3;
            const h = interpolate(raw, [-1, 1], [12, 160]);
            return (
              <div
                key={i}
                style={{
                  width: BAR_WIDTH,
                  height: h,
                  borderRadius: 6,
                  backgroundColor: WAVE_COLOR,
                }}
              />
            );
          })}
        </div>
      )}
    </AbsoluteFill>
  );
};
