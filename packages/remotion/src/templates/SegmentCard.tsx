import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { BulletOverlay } from "../components/overlays/BulletOverlay";
import { StatOverlay } from "../components/overlays/StatOverlay";
import { ChartOverlay } from "../components/overlays/ChartOverlay";

export interface WordTiming {
  word: string;
  speaker: string;
  start_ms: number;
  end_ms: number;
}

export interface SentenceTiming {
  words: WordTiming[];
  start_ms: number;
  end_ms: number;
  speaker: string;
}

export interface GraphicEntry {
  start_ms: number;
  end_ms: number;
  photo_url: string;
  query?: string;
}

export interface OverlayEntry {
  type: "bullets" | "stat" | "chart";
  show_ms: number;
  hide_ms: number;
  speakerColor: string;
  // bullets fields
  title?: string;
  items?: string[];
  // stat fields
  label?: string;
  value?: string;
  subtext?: string;
  // chart fields
  chart_type?: string;
  data_points?: { label: string; value: number }[];
}

export interface SegmentCardProps {
  title: string;
  bullets?: string[];
  photo_url?: string;
  host_a_name: string;
  host_b_name: string;
  host_a_color: string;
  host_b_color: string;
  current_speaker: "A" | "B" | "both";
  segment_number?: number;
  sentence_timings?: SentenceTiming[];
  graphics?: GraphicEntry[];
  overlays?: OverlayEntry[];
}

const TEXT = "#f5f0e8";

// Frames over which a sentence (or graphic) fades in/out at its boundaries.
const SENTENCE_FADE_FRAMES = 10;
const GRAPHIC_FADE_FRAMES = 15;
const OVERLAY_FADE_FRAMES = 20;

// Frames at the start/end of every card dedicated to the Ken Burns wipe
// transition. The same number of frames is used by the ffmpeg xfade overlap
// so the two baked transitions blend across the boundary.
const TRANSITION_FRAMES = 15;

export const SegmentCard: React.FC<SegmentCardProps> = ({
  title,
  photo_url,
  host_a_name,
  host_b_name,
  host_a_color,
  host_b_color,
  current_speaker,
  sentence_timings,
  graphics,
  overlays,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const currentMs = (frame / fps) * 1000;

  const hasPhoto = typeof photo_url === "string" && photo_url.trim().length > 0;

  // Ken Burns scale 1.00 -> 1.08 across full duration.
  const kenBurns = interpolate(
    frame,
    [0, Math.max(1, durationInFrames - 1)],
    [1.0, 1.08],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // Intro: spring-driven scale 0.9 → 1.0 plus opacity ramp 0 → 1 over the
  // first TRANSITION_FRAMES.
  const introSpring = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 120 },
  });
  const introScale = interpolate(introSpring, [0, 1], [0.9, 1.0]);
  const introOpacity = interpolate(frame, [0, TRANSITION_FRAMES], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Outro: linear 0 → 1 ramp over the last TRANSITION_FRAMES that drives a
  // 1.0 → 1.15 scale-up on the background and a 1 → 0 fade on the content.
  const outroProgress = interpolate(
    frame,
    [durationInFrames - TRANSITION_FRAMES, durationInFrames],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // Compose: Ken Burns pan layers on top of the transition scale, content
  // opacity is intro-in × outro-out.
  const transitionBgScale = introScale * (1 + outroProgress * 0.15);
  const contentOpacity = introOpacity * (1 - outroProgress);

  // Bottom speaker strip slides up over first 20 frames.
  const stripProgress = interpolate(frame, [0, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const stripTranslate = interpolate(stripProgress, [0, 1], [100, 0]);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#0f0f1f",
        overflow: "hidden",
        fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
      }}
    >
      {/* Background layer: scales with the intro/outro transition. The Ken
          Burns pan is nested INSIDE so it layers on top of the transition
          zoom. The dark overlay rides along since it scales fullscreen-to-
          fullscreen regardless. */}
      <AbsoluteFill
        style={{
          transform: `scale(${transitionBgScale})`,
          transformOrigin: "center",
        }}
      >
        {hasPhoto ? (
          <AbsoluteFill
            style={{
              transform: `scale(${kenBurns})`,
              transformOrigin: "center",
            }}
          >
            <Img
              src={photo_url}
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          </AbsoluteFill>
        ) : (
          <AbsoluteFill
            style={{
              background:
                "linear-gradient(135deg, #1a0b3d 0%, #2d1b69 45%, #0f2a5e 100%)",
            }}
          />
        )}

        {/* Dark overlay for readability. */}
        <AbsoluteFill style={{ backgroundColor: "rgba(0,0,0,0.55)" }} />
      </AbsoluteFill>

      {/* Content layer: fades in over TRANSITION_FRAMES and out over the
          last TRANSITION_FRAMES so the next card's intro blends in. */}
      <AbsoluteFill style={{ opacity: contentOpacity }}>
        {/* Context-aware center graphic. */}
        <CenterGraphic graphics={graphics ?? []} currentMs={currentMs} frame={frame} fps={fps} />

        {/* Context-aware overlays (bullets / stat / chart). */}
        <CenterOverlay overlays={overlays ?? []} currentMs={currentMs} frame={frame} fps={fps} />

        {/* Subtitle band: frosted slab above the speaker strip. */}
        <SubtitleBand
          sentences={sentence_timings ?? []}
          currentMs={currentMs}
          frame={frame}
          fps={fps}
          fallbackTitle={title}
          hostAColor={host_a_color}
          hostBColor={host_b_color}
        />

        {/* Bottom speaker strip. */}
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 0,
            height: 110,
            backgroundColor: "rgba(0,0,0,0.55)",
            backdropFilter: "blur(16px)",
            WebkitBackdropFilter: "blur(16px)",
            display: "flex",
            alignItems: "center",
            padding: "0 60px",
            gap: 36,
            transform: `translateY(${stripTranslate}%)`,
          }}
        >
          {current_speaker === "A" || current_speaker === "both" ? (
            <SpeakerChip name={host_a_name} color={host_a_color} />
          ) : null}
          {current_speaker === "B" || current_speaker === "both" ? (
            <SpeakerChip name={host_b_name} color={host_b_color} />
          ) : null}
          {current_speaker === "both" ? (
            <div
              style={{
                marginLeft: "auto",
                color: "rgba(245,240,232,0.5)",
                fontSize: 18,
                letterSpacing: 3,
                textTransform: "uppercase",
              }}
            >
              In Conversation
            </div>
          ) : null}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const SpeakerChip: React.FC<{ name: string; color: string }> = ({
  name,
  color,
}) => (
  <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
    <div
      style={{
        width: 20,
        height: 20,
        borderRadius: 10,
        backgroundColor: color,
        boxShadow: `0 0 16px ${color}`,
      }}
    />
    <div
      style={{
        fontSize: 32,
        fontWeight: 700,
        color: TEXT,
        letterSpacing: 1,
      }}
    >
      {name}
    </div>
  </div>
);

// Parse "#RRGGBB" into "r, g, b" for rgba() composition. Falls back to white.
const hexToRgb = (hex: string): string => {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return "255, 255, 255";
  const v = parseInt(m[1], 16);
  return `${(v >> 16) & 0xff}, ${(v >> 8) & 0xff}, ${v & 0xff}`;
};

interface SubtitleBandProps {
  sentences: SentenceTiming[];
  currentMs: number;
  frame: number;
  fps: number;
  fallbackTitle: string;
  hostAColor: string;
  hostBColor: string;
}

const SubtitleBand: React.FC<SubtitleBandProps> = ({
  sentences,
  currentMs,
  frame,
  fps,
  fallbackTitle,
  hostAColor,
  hostBColor,
}) => {
  // Frosted-glass slab pinned just above the bottom speaker strip. Height is
  // intentionally unbounded so the band grows to fit a long sentence.
  const bandStyle: React.CSSProperties = {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 110,
    paddingTop: 28,
    paddingBottom: 28,
    paddingLeft: 80,
    paddingRight: 80,
    backgroundColor: "rgba(0, 0, 0, 0.25)",
    backdropFilter: "blur(20px)",
    WebkitBackdropFilter: "blur(20px)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    boxSizing: "border-box",
  };

  // No sentence timings at all → fall back to the static segment title so the
  // band is never completely empty when alignment fails.
  if (!sentences || sentences.length === 0) {
    return (
      <div style={bandStyle}>
        <div
          style={{
            fontSize: 56,
            fontWeight: 600,
            color: "rgba(255,255,255,0.9)",
            letterSpacing: 0.5,
            textAlign: "center",
          }}
        >
          {fallbackTitle}
        </div>
      </div>
    );
  }

  // Find the active sentence (the one whose [start_ms, end_ms) covers
  // currentMs). When we're between sentences, render nothing — the band just
  // shows a frosted slab with no text.
  let activeIdx = -1;
  for (let i = 0; i < sentences.length; i++) {
    const s = sentences[i];
    if (s.start_ms <= currentMs && currentMs < s.end_ms) {
      activeIdx = i;
      break;
    }
  }
  if (activeIdx === -1) {
    return <div style={bandStyle} />;
  }

  const sentence = sentences[activeIdx];
  // Cross-fade in over the first SENTENCE_FADE_FRAMES of the sentence and out
  // over the last SENTENCE_FADE_FRAMES.
  const startFrame = (sentence.start_ms / 1000) * fps;
  const endFrame = (sentence.end_ms / 1000) * fps;
  const fadeIn = interpolate(
    frame,
    [startFrame, startFrame + SENTENCE_FADE_FRAMES],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const fadeOut = interpolate(
    frame,
    [endFrame - SENTENCE_FADE_FRAMES, endFrame],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const opacity = Math.min(fadeIn, fadeOut);

  return (
    <div style={bandStyle}>
      <div
        style={{
          display: "flex",
          flexDirection: "row",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "center",
          gap: 18,
          opacity,
          maxWidth: "100%",
        }}
      >
        {sentence.words.map((w, i) => {
          const speakerColor = w.speaker === "A" ? hostAColor : hostBColor;
          const rgb = hexToRgb(speakerColor);

          let color: string;
          let fontWeight = 500;
          let scale = 1;
          let textShadow = "none";

          if (w.end_ms < currentMs) {
            // Spoken.
            color = `rgba(${rgb}, 0.45)`;
          } else if (w.start_ms <= currentMs && currentMs <= w.end_ms) {
            // Active.
            color = `rgb(${rgb})`;
            fontWeight = 800;
            const popSpring = spring({
              frame: frame - (w.start_ms / 1000) * fps,
              fps,
              config: { damping: 12, stiffness: 180, mass: 0.4 },
            });
            scale = interpolate(popSpring, [0, 1], [1, 1.1]);
            textShadow = `0 0 14px rgba(${rgb}, 0.75), 0 2px 8px rgba(0,0,0,0.8)`;
          } else {
            // Upcoming.
            color = "rgba(255, 255, 255, 0.5)";
          }

          return (
            <span
              key={i}
              style={{
                fontSize: 56,
                fontWeight,
                color,
                transform: `scale(${scale})`,
                transformOrigin: "center",
                textShadow,
                display: "inline-block",
                whiteSpace: "nowrap",
                lineHeight: 1.15,
              }}
            >
              {w.word}
            </span>
          );
        })}
      </div>
    </div>
  );
};

interface CenterGraphicProps {
  graphics: GraphicEntry[];
  currentMs: number;
  frame: number;
  fps: number;
}

const CenterGraphic: React.FC<CenterGraphicProps> = ({
  graphics,
  currentMs,
  frame,
  fps,
}) => {
  if (!graphics || graphics.length === 0) return null;

  // Pick the first active graphic. With non-overlapping windows there's only
  // ever one candidate, but the linear scan is cheap regardless.
  let active: GraphicEntry | null = null;
  for (const g of graphics) {
    if (g.start_ms <= currentMs && currentMs < g.end_ms) {
      active = g;
      break;
    }
  }
  if (!active || !active.photo_url) return null;

  const startFrame = (active.start_ms / 1000) * fps;
  const endFrame = (active.end_ms / 1000) * fps;
  const fadeIn = interpolate(
    frame,
    [startFrame, startFrame + GRAPHIC_FADE_FRAMES],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const fadeOut = interpolate(
    frame,
    [endFrame - GRAPHIC_FADE_FRAMES, endFrame],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const opacity = Math.min(fadeIn, fadeOut);

  // Card sits roughly in the upper-middle of the screen. It's bounded so it
  // never collides with the subtitle band (bottom 110+ px is owned by the
  // strip + subtitles).
  return (
    <div
      style={{
        position: "absolute",
        top: "12%",
        left: "50%",
        transform: "translateX(-50%)",
        width: "60%",
        maxWidth: "60%",
        height: "45%",
        maxHeight: "45%",
        borderRadius: 16,
        overflow: "hidden",
        border: "1px solid rgba(255,255,255,0.15)",
        boxShadow: "0 16px 48px rgba(0,0,0,0.55)",
        opacity,
      }}
    >
      <Img
        src={active.photo_url}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          display: "block",
        }}
      />
    </div>
  );
};

interface CenterOverlayProps {
  overlays: OverlayEntry[];
  currentMs: number;
  frame: number;
  fps: number;
}

const CenterOverlay: React.FC<CenterOverlayProps> = ({
  overlays,
  currentMs,
  frame,
  fps,
}) => {
  if (!overlays || overlays.length === 0) return null;

  // Pick the first active overlay. Overlays are expected to be non-overlapping;
  // a linear scan is fine for the small N.
  let active: OverlayEntry | null = null;
  for (const o of overlays) {
    if (o.show_ms <= currentMs && currentMs < o.hide_ms) {
      active = o;
      break;
    }
  }
  if (!active) return null;

  const startFrame = (active.show_ms / 1000) * fps;
  const endFrame = (active.hide_ms / 1000) * fps;
  const fadeIn = interpolate(
    frame,
    [startFrame, startFrame + OVERLAY_FADE_FRAMES],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const fadeOut = interpolate(
    frame,
    [endFrame - OVERLAY_FADE_FRAMES, endFrame],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const wrapperOpacity = Math.min(fadeIn, fadeOut);

  // Progress 0→1 across the overlay's [show_ms, hide_ms) window. Inner
  // components key their stagger animations off this so they're independent
  // of absolute frame.
  const span = Math.max(1, active.hide_ms - active.show_ms);
  const progress = Math.max(0, Math.min(1, (currentMs - active.show_ms) / span));

  // Center the overlay between the title pill at top and the subtitle band at
  // the bottom (~220px reserved for subtitles + speaker strip).
  const wrapper: React.CSSProperties = {
    position: "absolute",
    top: 100,
    left: 0,
    right: 0,
    bottom: 220,
    pointerEvents: "none",
    opacity: wrapperOpacity,
  };

  return (
    <div style={wrapper}>
      {active.type === "bullets" ? (
        <BulletOverlay
          title={active.title || ""}
          items={active.items || []}
          progress={progress}
          speakerColor={active.speakerColor}
        />
      ) : null}
      {active.type === "stat" ? (
        <StatOverlay
          label={active.label || ""}
          value={active.value || ""}
          subtext={active.subtext}
          progress={progress}
          speakerColor={active.speakerColor}
        />
      ) : null}
      {active.type === "chart" ? (
        <ChartOverlay
          title={active.title || ""}
          data_points={active.data_points || []}
          chart_type={active.chart_type || "growth"}
          progress={progress}
          speakerColor={active.speakerColor}
        />
      ) : null}
    </div>
  );
};
