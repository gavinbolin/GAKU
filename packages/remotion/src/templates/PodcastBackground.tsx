import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

/**
 * PodcastBackground — the looped base layer for a podcast video.
 *
 * Shows the podcast topic + a subtle animated "waveform" placeholder, so the
 * stretches between scripted visuals don't show a static frame for minutes at
 * a time. Designed to be loopable (no abrupt visual reset on loop boundary).
 *
 * Rendered once at ~6-10s duration; compose_final_video then loops it to fill
 * the full podcast runtime.
 */

export interface PodcastBackgroundProps {
  topic: string;
  subtitle?: string;
}

const BG_TOP = "#0f1020";
const BG_BOTTOM = "#1a1a2e";
const TEXT = "#f5f0e8";
const ACCENT = "#7C3AED";
const WAVE_COLOR = "rgba(124, 58, 237, 0.55)";

const BAR_COUNT = 60;
const BAR_WIDTH = 12;
const BAR_GAP = 18;

export const PodcastBackground: React.FC<PodcastBackgroundProps> = ({
  topic,
  subtitle = "PROJECT GAKU",
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  // Pseudo-random bar heights driven by sin waves at different frequencies.
  // Looks like an audio meter without actually being one — fine for the
  // base layer since real audio is the foreground sound, not the visual.
  //
  // Loop-snap: ffmpeg loops this composition with -stream_loop -1, so the
  // last rendered frame is immediately followed by the first. We drive each
  // sin() with `phaseCycle = (frame / durationInFrames) * 2π`, then multiply
  // by an INTEGER harmonic count. Result: `sin(n * phaseCycle)` has the same
  // value at frame 0 and frame durationInFrames for every integer n, so the
  // loop seam is invisible.
  const bars = React.useMemo(() => Array.from({ length: BAR_COUNT }, (_, i) => i), []);
  const phaseCycle = (frame / Math.max(1, durationInFrames)) * Math.PI * 2;

  return (
    <AbsoluteFill
      style={{
        background: `linear-gradient(180deg, ${BG_TOP} 0%, ${BG_BOTTOM} 100%)`,
        fontFamily: "Georgia, 'Times New Roman', serif",
        color: TEXT,
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
            opacity: 0.85,
            letterSpacing: 6,
            textTransform: "uppercase",
            color: ACCENT,
            marginBottom: 36,
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
        }}
      >
        {topic}
      </div>

      {/* Pseudo-waveform along the bottom — purely decorative. */}
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
          // Each bar oscillates with its own phase + integer harmonic count,
          // so the row looks lively AND seams cleanly at the loop point.
          // Harmonics 2, 3, 1 over a single loop pass.
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
    </AbsoluteFill>
  );
};
