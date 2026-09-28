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
import { StatHero, type StatCompanion } from "../components/overlays/StatHero";
import { ChapterTitleOverlay } from "../components/overlays/ChapterTitleOverlay";
import {
  DiagramOverlay,
  type DiagramColumn,
  type DiagramNode,
  type QuadrantData,
} from "../components/overlays/DiagramOverlay";
import { PodcastBackground } from "./PodcastBackground";

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
  title?: string;
  items?: string[];
  label?: string;
  value?: string;
  subtext?: string;
  chart_type?: string;
  data_points?: { label: string; value: number }[];
}

/**
 * One entry of the in-segment beat timeline (visual_timeline.plan_episode).
 * Beats never overlap each other, so a long card can't mask a stat.
 */
export interface BeatEntry {
  type: "chapter" | "stat" | "diagram" | "bullets" | "chart" | "broll";
  show_ms: number;
  hide_ms: number;
  speakerColor?: string;
  title?: string;
  subtitle?: string;
  chapter_number?: number;
  accent?: string;
  label?: string;
  value?: string;
  subtext?: string;
  companions?: StatCompanion[];
  kind?: string;
  nodes?: DiagramNode[];
  center?: string;
  columns?: DiagramColumn[];
  /** kind="quadrant": 2x2 strategy matrix (2026-09-28 item 2). */
  quadrant?: QuadrantData;
  items?: string[];
  chart_type?: string;
  data_points?: { label: string; value: number }[];
  photo_url?: string;
  query?: string;
  /** stat: card-relative ms when the number is spoken (count-up lands by then). */
  land_ms?: number;
  /** stat: planner verdict (false for ranges / limits / late cards => static snap). */
  count_up?: boolean;
  static?: boolean;
}

export interface BackdropEntry {
  start_ms: number;
  end_ms: number;
  photo_url: string;
  query?: string;
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
  beats?: BeatEntry[];
  backdrops?: BackdropEntry[];
}

const TEXT = "#f5f0e8";
const SENTENCE_FADE_FRAMES = 3;
const GRAPHIC_FADE_FRAMES = 15;
const OVERLAY_FADE_FRAMES = 20;
const TRANSITION_FRAMES = 15;

const resolveLiveSpeaker = (
  sentences: SentenceTiming[] | undefined,
  currentMs: number,
  fallback: "A" | "B" | "both",
): "A" | "B" | "both" => {
  if (!sentences || sentences.length === 0) return fallback;
  let last: "A" | "B" | null = null;
  for (const s of sentences) {
    const sp: "A" | "B" = s.speaker === "B" ? "B" : "A";
    if (s.start_ms <= currentMs) {
      last = sp;
      if (currentMs < s.end_ms) return sp;
    }
  }
  return last ?? fallback;
};


const speakerBlend = (sentences: SentenceTiming[], currentMs: number): number => {
  // 0 = host A, 1 = host B. Smoothstep over 420ms after a switch.
  let target = 0;
  let switchAt = -99999;
  for (const s of sentences) {
    if (s.start_ms > currentMs) break;
    const next = s.speaker === "B" ? 1 : 0;
    if (next !== target) {
      target = next;
      switchAt = s.start_ms;
    }
  }
  const t = Math.max(0, Math.min(1, (currentMs - switchAt) / 420));
  const eased = t * t * (3 - 2 * t);
  return (1 - target) * (1 - eased) + target * eased;
};

const MIN_CUE_MS = 2200;
const TINY_MS = 800;
const TINY_WORDS = 2;
const MERGE_GAP_MS = 480;

const buildSubtitleCues = (sentences: SentenceTiming[]): SentenceTiming[] => {
  if (!sentences.length) return [];
  const merged: SentenceTiming[] = [];
  for (const s of sentences) {
    const last = merged[merged.length - 1];
    const dur = s.end_ms - s.start_ms;
    const tiny = s.words.length <= TINY_WORDS || dur < TINY_MS;
    if (last && tiny && s.speaker === last.speaker) {
      last.words = [...last.words, ...s.words];
      last.end_ms = Math.max(last.end_ms, s.end_ms);
      continue;
    }
    if (
      last &&
      s.speaker === last.speaker &&
      s.start_ms - last.end_ms <= MERGE_GAP_MS &&
      last.words.length < 14
    ) {
      last.words = [...last.words, ...s.words];
      last.end_ms = Math.max(last.end_ms, s.end_ms);
      continue;
    }
    merged.push({ ...s, words: [...s.words] });
  }
  return merged;
};

const pickCue = (
  cues: SentenceTiming[],
  currentMs: number,
): { cue: SentenceTiming | null; held: boolean } => {
  if (!cues.length) return { cue: null, held: false };
  let idx = -1;
  for (let i = 0; i < cues.length; i++) {
    if (cues[i].start_ms <= currentMs) idx = i;
  }
  if (idx < 0) return { cue: null, held: false };
  const cue = cues[idx];
  const next = cues[idx + 1];
  if (next && currentMs >= next.start_ms) {
    return { cue: next, held: currentMs >= next.end_ms };
  }
  const held = currentMs >= cue.end_ms;
  // Keep a short cue up until the next line, and at least MIN_CUE_MS.
  if (next && currentMs < next.start_ms) return { cue, held };
  if (!next && currentMs < cue.start_ms + MIN_CUE_MS) return { cue, held };
  return { cue, held };
};

export const SegmentCard: React.FC<SegmentCardProps> = ({
  title,
  bullets,
  photo_url,
  host_a_name,
  host_b_name,
  host_a_color,
  host_b_color,
  current_speaker,
  segment_number,
  sentence_timings,
  graphics,
  overlays,
  beats,
  backdrops,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const currentMs = (frame / fps) * 1000;

  const hasPhoto = typeof photo_url === "string" && photo_url.trim().length > 0;
  const graphicsList = graphics ?? [];
  const overlaysList = overlays ?? [];
  const liveSpeaker = resolveLiveSpeaker(
    sentence_timings,
    currentMs,
    current_speaker,
  );
  const cues = React.useMemo(
    () => buildSubtitleCues(sentence_timings ?? []),
    [sentence_timings],
  );
  const { cue, held } = pickCue(cues, currentMs);
  const speakMix = speakerBlend(sentence_timings ?? [], currentMs);
  const talkingPoints =
    bullets && bullets.length > 0
      ? bullets
      : [
          title,
          liveSpeaker === "B" ? "Co-host responding" : "Host laying out the problem",
        ];

  const hasActiveGraphic = graphicsList.some(
    (g) =>
      g.start_ms <= currentMs && currentMs < g.end_ms && Boolean(g.photo_url),
  );
  const hasActiveOverlay = overlaysList.some(
    (o) => o.show_ms <= currentMs && currentMs < o.hide_ms,
  );
  const beatList = beats ?? [];
  const hasBeats = beatList.length > 0;
  const activeBeat =
    beatList.find((b) => b.show_ms <= currentMs && currentMs < b.hide_ms) ?? null;
  const backdropList: BackdropEntry[] =
    backdrops && backdrops.length > 0
      ? backdrops
      : hasPhoto
        ? [{ start_ms: 0, end_ms: (durationInFrames / fps) * 1000, photo_url: photo_url as string }]
        : [];
  const showStageCard =
    !activeBeat &&
    backdropList.length === 0 &&
    !hasActiveGraphic &&
    !hasActiveOverlay &&
    !hasPhoto;
  const chapterActive = activeBeat?.type === "chapter";

  const kenBurns = interpolate(
    frame,
    [0, Math.max(1, durationInFrames - 1)],
    [1.0, 1.08],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

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

  const outroProgress = interpolate(
    frame,
    [durationInFrames - TRANSITION_FRAMES, durationInFrames],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const transitionBgScale = introScale * (1 + outroProgress * 0.15);
  const contentOpacity = introOpacity * (1 - outroProgress);

  const stripProgress = interpolate(frame, [0, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const stripTranslate = interpolate(stripProgress, [0, 1], [100, 0]);

  const titleLabel = segment_number
    ? `${String(segment_number).padStart(2, "0")}  \u00b7  ${title}`
    : title;


  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#0f0f1f",
        overflow: "hidden",
        fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
      }}
    >
      <AbsoluteFill
        style={{
          transform: `scale(${transitionBgScale})`,
          transformOrigin: "center",
        }}
      >
        {backdropList.length > 0 ? (
          <BackdropLayer backdrops={backdropList} currentMs={currentMs} kenBurnsFallback={kenBurns} />
        ) : (
          <PodcastBackground topic={title} subtitle="" variant="backdrop" />
        )}

        <AbsoluteFill
          style={{ backgroundColor: backdropList.length > 0 ? "rgba(0,0,0,0.5)" : "rgba(0,0,0,0.22)" }}
        />
      </AbsoluteFill>

      <AbsoluteFill style={{ opacity: contentOpacity }}>
        {showStageCard ? (
          <StageBoard
            frame={frame}
            fps={fps}
            title={title}
            talkingPoints={talkingPoints}
            cue={cue}
            hostAName={host_a_name}
            hostBName={host_b_name}
            hostAColor={host_a_color}
            hostBColor={host_b_color}
            liveSpeaker={liveSpeaker}
            speakMix={speakMix}
          />
        ) : null}

        {hasBeats ? (
          <BeatsLayer beats={beatList} currentMs={currentMs} fallbackColor={host_a_color} />
        ) : (
          <>
            <CenterGraphic graphics={graphicsList} currentMs={currentMs} frame={frame} fps={fps} />
            <CenterOverlay overlays={overlaysList} currentMs={currentMs} frame={frame} fps={fps} />
          </>
        )}

        {/* 2026-09-25 item 9: segment label soft-killed to small corner chrome
            (was a big centered all-caps pill that read like a slide deck). */}
        <div
          style={{
            position: "absolute",
            top: 34,
            left: 48,
            maxWidth: "46%",
            pointerEvents: "none",
            opacity: chapterActive ? 0 : 0.78,
          }}
        >
          <div
            style={{
              fontSize: 17,
              fontWeight: 600,
              letterSpacing: 2.2,
              textTransform: "uppercase",
              color: "rgba(245,240,232,0.82)",
              padding: "7px 16px",
              borderRadius: 999,
              backgroundColor: "rgba(0,0,0,0.28)",
              textShadow: "0 2px 8px rgba(0,0,0,0.6)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {titleLabel}
          </div>
        </div>

        <SubtitleBand
          cue={cue}
          held={held}
          currentMs={currentMs}
          frame={frame}
          fps={fps}
          fallbackTitle={title}
          hostAColor={host_a_color}
          hostBColor={host_b_color}
        />

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
            gap: 48,
            transform: `translateY(${stripTranslate}%)`,
          }}
        >
          <SpeakerChip
            name={host_a_name}
            color={host_a_color}
            active={liveSpeaker !== "B"}
            frame={frame}
            energy={1 - speakMix}
          />
          <SpeakerChip
            name={host_b_name}
            color={host_b_color}
            active={liveSpeaker !== "A"}
            frame={frame}
            energy={speakMix}
          />
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const MINI_BARS = 6;
const MINI_BAR_W = 3;
const MINI_GAP = 3;
const MINI_H = 24;
const MINI_W = MINI_BARS * MINI_BAR_W + (MINI_BARS - 1) * MINI_GAP;

const mixHexToward = (hex: string, toward: string, amount: number): string => {
  const parse = (h: string) => {
    const m = /^#?([0-9a-f]{6})$/i.exec(h.trim());
    if (!m) return [255, 255, 255];
    const v = parseInt(m[1], 16);
    return [(v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff];
  };
  const a = parse(hex);
  const b = parse(toward);
  const t = Math.max(0, Math.min(1, amount));
  const rgb = a.map((c, i) => Math.round(c + (b[i] - c) * t));
  return `#${rgb.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
};

const MiniWave: React.FC<{
  frame: number;
  color: string;
  energy: number;
}> = ({ frame, color, energy }) => {
  // Idle bars keep a muted tint of the host color so A/B stay distinct even
  // when the other host is talking; active bars use the full brand color.
  const barColor = energy > 0.45 ? color : mixHexToward(color, "#1a1a2e", 0.55);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: MINI_GAP,
        width: MINI_W,
        height: MINI_H,
        flexShrink: 0,
      }}
    >
      {Array.from({ length: MINI_BARS }).map((_, i) => {
        const phase = i * 0.17;
        const a = Math.sin(frame * (0.18 + energy * 0.12) + phase);
        const b = Math.sin(frame * (0.31 + energy * 0.08) + phase * 1.7);
        const raw = 0.35 + 0.65 * (0.5 + 0.5 * (a * 0.65 + b * 0.35));
        const h = Math.max(3, raw * MINI_H * (0.12 + 0.88 * energy));
        return (
          <div
            key={i}
            style={{
              width: MINI_BAR_W,
              height: h,
              borderRadius: 1,
              backgroundColor: barColor,
              opacity: 0.4 + 0.6 * Math.max(energy, 0.25),
              boxShadow: energy > 0.55 ? `0 0 ${6 + energy * 8}px ${color}` : "none",
            }}
          />
        );
      })}
    </div>
  );
};

const SpeakerChip: React.FC<{
  name: string;
  color: string;
  active: boolean;
  frame: number;
  energy: number;
}> = ({ name, color, active, frame, energy }) => {
  const pulse = active ? 1 + 0.05 * Math.sin(frame * 0.22) : 1;
  const targetBright = active ? 1 : 0.42;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
      }}
    >
      <MiniWave frame={frame} color={color} energy={energy} />
      <div
        style={{
          fontSize: 32,
          fontWeight: active ? 800 : 600,
          color: active ? TEXT : "rgba(245,240,232,0.7)",
          letterSpacing: 1,
          textShadow: active ? `0 0 16px ${color}` : "none",
          opacity: targetBright,
          transform: `scale(${active ? pulse : 0.96})`,
          transformOrigin: "left center",
          filter: active ? "none" : "saturate(0.7)",
        }}
      >
        {name}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// LOCKED DEFAULTS (Gavin, 2026-09-28 item 8) -- karaoke + host colours.
// No flags / props toggle these; every episode renders this way:
//   * current-word karaoke: active word takes the SPEAKER's colour (host A/B
//     colours come from PROJECT GAKU/voice_colors.json via visual_queue.
//     voice_persona -- the single voice->colour registry),
//   * grow via transform: scale() only (layout-stable, weight fixed at 800),
//   * NO white pill / background box behind the active word.
// See docs/visual-defaults.md before changing anything here.
// ---------------------------------------------------------------------------
const FALLBACK_ACCENT = "#7C3AED";
const INACTIVE_WORD_COLOR = "#F5F0E8";
/** Soft edges so adjacent karaoke words cross-fade instead of hard-cutting. */
const KARAOKE_FADE_IN_MS = 140;
const KARAOKE_FADE_OUT_MS = 180;
const KARAOKE_SCALE_PEAK = 1.1;
const KARAOKE_FONT_PX = 52;
/** Flex gap between word boxes (each box also carries its own grow padding). */
const KARAOKE_WORD_GAP = 10;
/**
 * 2026-09-25 collision fix: every word box ALWAYS carries horizontal padding
 * sized for its own peak grow, so a scaled active word never reaches its
 * neighbours. The padding depends only on the word text (never on active
 * state), so line breaks are identical whether or not a word is highlighted.
 *
 * Width is a deliberately generous estimate for the 800-weight UI font.
 * transform:scale also scales the padding, so solve
 *   pad >= (peak-1) * (w/2 + pad)  =>  pad >= (peak-1) * w / 2 / (2 - peak)
 */
const estimateKaraokeWordPx = (word: string): number => {
  let em = 0;
  for (const ch of word) {
    if ("iljtfI.,;:'!|\u2019()[]\"".includes(ch)) em += 0.34;
    else if ("r-".includes(ch)) em += 0.44;
    else if ("\u2014\u2013mwMW@%".includes(ch)) em += 0.92;
    else if (ch >= "A" && ch <= "Z") em += 0.7;
    else em += 0.58;
  }
  return em * KARAOKE_FONT_PX + 4;
};
const karaokeGrowPadPx = (word: string): number =>
  Math.ceil(
    ((KARAOKE_SCALE_PEAK - 1) * estimateKaraokeWordPx(word)) / 2 / (2 - KARAOKE_SCALE_PEAK),
  ) + 3;
/** Side inset + caption width leave slack so peak-scale / weight never reflows wraps. */
const KARAOKE_BAND_PAD_X = 140;
const KARAOKE_CAPTION_MAX_WIDTH = "88%";

const hexToRgb = (hex: string): string => {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return "255, 255, 255";
  const v = parseInt(m[1], 16);
  return `${(v >> 16) & 0xff}, ${(v >> 8) & 0xff}, ${v & 0xff}`;
};

const parseHexRgb = (hex: string): [number, number, number] => {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return [245, 240, 232];
  const v = parseInt(m[1], 16);
  return [(v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff];
};

const lerpHex = (from: string, to: string, t: number): string => {
  const clamped = Math.max(0, Math.min(1, t));
  const [ar, ag, ab] = parseHexRgb(from);
  const [br, bg, bb] = parseHexRgb(to);
  const r = Math.round(ar + (br - ar) * clamped);
  const g = Math.round(ag + (bg - ag) * clamped);
  const b = Math.round(ab + (bb - ab) * clamped);
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
};

const smoothstep01 = (t: number): number => {
  const x = Math.max(0, Math.min(1, t));
  return x * x * (3 - 2 * x);
};

const resolveWordSpeakerColor = (
  speaker: string,
  hostAColor: string,
  hostBColor: string,
): string => {
  if (speaker === "B") return hostBColor || FALLBACK_ACCENT;
  if (speaker === "A") return hostAColor || FALLBACK_ACCENT;
  return hostAColor || FALLBACK_ACCENT;
};

interface SubtitleBandProps {
  cue: SentenceTiming | null;
  held: boolean;
  currentMs: number;
  frame: number;
  fps: number;
  fallbackTitle: string;
  hostAColor: string;
  hostBColor: string;
}

const SubtitleBand: React.FC<SubtitleBandProps> = ({
  cue,
  held,
  currentMs,
  frame,
  fps,
  fallbackTitle,
  hostAColor,
  hostBColor,
}) => {
  const bandStyle: React.CSSProperties = {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 110,
    paddingTop: 28,
    paddingBottom: 28,
    paddingLeft: KARAOKE_BAND_PAD_X,
    paddingRight: KARAOKE_BAND_PAD_X,
    backgroundColor: "rgba(0, 0, 0, 0.62)",
    backdropFilter: "blur(20px)",
    WebkitBackdropFilter: "blur(20px)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    boxSizing: "border-box",
  };

  if (!cue) {
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

  const lastIdx = cue.words.length - 1;

  return (
    <div style={bandStyle}>
      <div
        style={{
          display: "flex",
          flexDirection: "row",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "center",
          columnGap: KARAOKE_WORD_GAP,
          rowGap: 18,
          maxWidth: KARAOKE_CAPTION_MAX_WIDTH,
        }}
      >
        {cue.words.map((w, i) => {
          const speakerColor = resolveWordSpeakerColor(
            w.speaker,
            hostAColor,
            hostBColor,
          );
          const rgb = hexToRgb(speakerColor);
          // Hold the highlight until the next word begins (or cue end) so
          // short Whisper word-spans don't blink off mid-syllable.
          const nextStart =
            i + 1 < cue.words.length
              ? cue.words[i + 1].start_ms
              : Math.max(w.end_ms, cue.end_ms);
          const activeUntil = Math.max(
            w.end_ms,
            Math.min(nextStart, w.end_ms + 280),
          );
          const isLastHeld = held && i === lastIdx;

          // Continuous 0..1 activation with soft edges so scale/color ease
          // across adjacent words instead of hard-cutting.
          let activation = 0;
          if (isLastHeld) {
            activation = 1;
          } else if (!held) {
            if (
              currentMs >= w.start_ms - KARAOKE_FADE_IN_MS &&
              currentMs < w.start_ms
            ) {
              activation = interpolate(
                currentMs,
                [w.start_ms - KARAOKE_FADE_IN_MS, w.start_ms],
                [0, 1],
                { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
              );
            } else if (currentMs >= w.start_ms && currentMs < activeUntil) {
              activation = 1;
            } else if (
              currentMs >= activeUntil &&
              currentMs < activeUntil + KARAOKE_FADE_OUT_MS
            ) {
              activation = interpolate(
                currentMs,
                [activeUntil, activeUntil + KARAOKE_FADE_OUT_MS],
                [1, 0],
                { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
              );
            }
          }

          const eased = smoothstep01(activation);

          // Softer spring enter; gated by eased so exit also ramps down.
          const popSpring = spring({
            frame: Math.max(0, frame - (w.start_ms / 1000) * fps),
            fps,
            config: { damping: 18, stiffness: 120, mass: 0.5 },
          });
          const springBoost = interpolate(popSpring, [0, 1], [1, KARAOKE_SCALE_PEAK]);
          const scale = 1 + (springBoost - 1) * eased;

          // Current-word-only karaoke: no white pill / background highlight.
          // Active word takes the speaker color (host waveform color); inactive stays cream.
          const color = lerpHex(INACTIVE_WORD_COLOR, speakerColor, eased);
          // Lock weight: 600→800 widens glyphs and reflows flex-wrap lines.
          // Grow is transform:scale only (layout width stays at unscaled metrics).
          const fontWeight = 800;
          const glowAlpha = 0.45 * eased;
          const textShadow =
            eased > 0.02
              ? `0 0 ${6 + 14 * eased}px rgba(${rgb}, ${glowAlpha}), 0 2px 10px rgba(0,0,0,0.9)`
              : "0 2px 12px rgba(0,0,0,0.95)";

          // Fixed per-word grow padding (text-only, never active-dependent).
          const growPad = karaokeGrowPadPx(w.word);
          return (
            <span
              key={i}
              style={{
                fontSize: KARAOKE_FONT_PX,
                fontWeight,
                color,
                backgroundColor: "transparent",
                padding: `0px ${growPad}px 4px ${growPad}px`,
                borderRadius: 0,
                boxShadow: "none",
                // Layout-stable grow: scale does not affect flex-wrap flow.
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


const StageBoard: React.FC<{
  frame: number;
  fps: number;
  title: string;
  talkingPoints: string[];
  cue: SentenceTiming | null;
  hostAName: string;
  hostBName: string;
  hostAColor: string;
  hostBColor: string;
  liveSpeaker: "A" | "B" | "both";
  speakMix: number;
}> = ({
  frame,
  fps,
  talkingPoints,
  cue,
  hostAName,
  hostBName,
  hostAColor,
  hostBColor,
  liveSpeaker,
  speakMix,
}) => {
  const intro = spring({
    frame,
    fps,
    config: { damping: 16, stiffness: 120, mass: 0.7 },
  });
  const enter = interpolate(intro, [0, 1], [24, 0]);
  const op = interpolate(intro, [0, 1], [0, 1]);

  return (
    <div
      style={{
        position: "absolute",
        top: 120,
        left: 64,
        right: 64,
        bottom: 280,
        display: "flex",
        gap: 36,
        opacity: op,
        transform: `translateY(${enter}px)`,
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          width: 380,
          display: "flex",
          flexDirection: "column",
          gap: 18,
          justifyContent: "center",
        }}
      >
        <StageSpeakerRow
          name={hostAName}
          role="Host"
          color={hostAColor}
          energy={1 - speakMix}
          frame={frame}
        />
        <StageSpeakerRow
          name={hostBName}
          role="Co-Host"
          color={hostBColor}
          energy={speakMix}
          frame={frame}
        />
      </div>

      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: 20,
          justifyContent: "center",
          minWidth: 0,
        }}
      >
        <div
          style={{
            padding: "28px 32px",
            borderRadius: 24,
            background:
              "linear-gradient(180deg, rgba(18,18,40,0.78) 0%, rgba(8,8,20,0.58) 100%)",
            border: "1px solid rgba(233,213,255,0.22)",
            boxShadow: "0 18px 50px rgba(0,0,0,0.4)",
          }}
        >
          <div
            style={{
              fontSize: 14,
              letterSpacing: 4,
              textTransform: "uppercase",
              color: "rgba(233,213,255,0.85)",
              fontWeight: 700,
              marginBottom: 14,
            }}
          >
            In this segment
          </div>
          {talkingPoints.slice(0, 3).map((pt, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 14,
                marginTop: i === 0 ? 0 : 14,
              }}
            >
              <div
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: 5,
                  marginTop: 10,
                  backgroundColor: i === 0 ? hostAColor : hostBColor,
                  flexShrink: 0,
                }}
              />
              <div
                style={{
                  fontSize: 28,
                  fontWeight: 600,
                  color: TEXT,
                  lineHeight: 1.25,
                }}
              >
                {pt}
              </div>
            </div>
          ))}
        </div>

        <div style={{ flex: 1, minHeight: 120, display: "flex", alignItems: "center" }}>
          <DualWaveform
            frame={frame}
            colorA={hostAColor}
            colorB={hostBColor}
            speakMix={speakMix}
          />
        </div>
      </div>
    </div>
  );
};

const DualWaveform: React.FC<{
  frame: number;
  colorA: string;
  colorB: string;
  speakMix: number;
  compact?: boolean;
}> = ({ frame, colorA, colorB, speakMix, compact = false }) => {
  const bars = compact ? 36 : 52;
  const energyA = 1 - speakMix;
  const energyB = speakMix;
  const scaleA = 0.42 + 0.9 * energyA;
  const scaleB = 0.42 + 0.9 * energyB;
  const hBase = compact ? 28 : 52;
  const padY = compact ? 8 : 14;

  return (
    <div
      style={{
        width: "100%",
        padding: `${padY}px 4px`,
        display: "flex",
        flexDirection: "column",
        alignItems: "stretch",
        justifyContent: "center",
        gap: compact ? 4 : 8,
      }}
    >
      <WaveRow
        frame={frame}
        color={colorA}
        energy={energyA}
        scale={scaleA}
        bars={bars}
        baseH={hBase}
        seed={0.17}
      />
      <WaveRow
        frame={frame}
        color={colorB}
        energy={energyB}
        scale={scaleB}
        bars={bars}
        baseH={hBase}
        seed={0.31}
        mirror
      />
    </div>
  );
};

const WaveRow: React.FC<{
  frame: number;
  color: string;
  energy: number;
  scale: number;
  bars: number;
  baseH: number;
  seed: number;
  mirror?: boolean;
}> = ({ frame, color, energy, scale, bars, baseH, seed, mirror = false }) => {
  return (
    <div
      style={{
        display: "flex",
        alignItems: mirror ? "flex-start" : "flex-end",
        justifyContent: "space-between",
        height: baseH * (0.55 + 0.9 * energy),
        opacity: 0.28 + 0.72 * energy,
        transform: `scaleY(${scale})`,
        transformOrigin: mirror ? "top center" : "bottom center",
      }}
    >
      {Array.from({ length: bars }).map((_, i) => {
        const phase = i * seed;
        const a = Math.sin(frame * (0.18 + energy * 0.12) + phase);
        const b = Math.sin(frame * (0.31 + energy * 0.08) + phase * 1.7);
        const raw = 0.35 + 0.65 * (0.5 + 0.5 * (a * 0.65 + b * 0.35));
        const h = Math.max(4, raw * baseH * (0.45 + 0.7 * energy));
        return (
          <div
            key={i}
            style={{
              width: 6,
              height: h,
              borderRadius: 3,
              backgroundColor: color,
              boxShadow: energy > 0.55 ? `0 0 ${8 + energy * 10}px ${color}` : "none",
            }}
          />
        );
      })}
    </div>
  );
};

const StageSpeakerRow: React.FC<{
  name: string;
  role: string;
  color: string;
  energy: number;
  frame: number;
}> = ({ name, role, color, energy, frame }) => {
  const pulse = 1 + 0.03 * energy * Math.sin(frame * 0.2);
  const glow = 0.12 + energy * (0.5 + 0.4 * (0.5 + 0.5 * Math.sin(frame * 0.2)));
  const avatar = 64 + 20 * energy;
  const nameSize = 28 + 8 * energy;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 20,
        padding: `${16 + 6 * energy}px 22px`,
        borderRadius: 22,
        background: `linear-gradient(180deg, rgba(18,18,40,${0.35 + 0.45 * energy}) 0%, rgba(8,8,20,${0.3 + 0.3 * energy}) 100%)`,
        border: `1px solid ${energy > 0.45 ? color + "cc" : "rgba(255,255,255,0.08)"}`,
        boxShadow: energy > 0.4 ? `0 0 ${22 + glow * 20}px ${color}99` : "none",
        transform: `scale(${pulse})`,
        transformOrigin: "left center",
        opacity: 0.5 + 0.5 * energy,
      }}
    >
      <div
        style={{
          width: avatar,
          height: avatar,
          borderRadius: 999,
          backgroundColor: color,
          boxShadow: `0 0 ${12 + 16 * energy}px ${color}`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          fontFamily: "Georgia, 'Times New Roman', serif",
          fontSize: 28 + 8 * energy,
          fontWeight: 700,
          color: "#0a0a16",
        }}
      >
        {name.slice(0, 1).toUpperCase()}
      </div>
      <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div
          style={{
            fontSize: 13,
            letterSpacing: 3,
            textTransform: "uppercase",
            color,
            fontWeight: 700,
          }}
        >
          {role}
        </div>
        <div
          style={{
            fontFamily: "Georgia, 'Times New Roman', serif",
            fontSize: nameSize,
            fontWeight: 700,
            color: TEXT,
            lineHeight: 1.1,
          }}
        >
          {name}
        </div>
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

  const span = Math.max(1, active.hide_ms - active.show_ms);
  const progress = Math.max(0, Math.min(1, (currentMs - active.show_ms) / span));

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
          localMs={currentMs - active.show_ms}
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


// ---------------------------------------------------------------------------
// 2026-09-25 beat timeline layers (chapter / stat / diagram / bullets / chart /
// b-roll) + rotating backdrops. Rendered below the karaoke SubtitleBand.
// ---------------------------------------------------------------------------

const BEAT_FADE_MS = 380;
const BACKDROP_XFADE_MS = 900;

const beatOpacity = (b: { show_ms: number; hide_ms: number }, currentMs: number): number => {
  const fin = interpolate(currentMs, [b.show_ms, b.show_ms + BEAT_FADE_MS], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const fout = interpolate(currentMs, [b.hide_ms - BEAT_FADE_MS, b.hide_ms], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return Math.min(fin, fout);
};

const BackdropLayer: React.FC<{
  backdrops: BackdropEntry[];
  currentMs: number;
  kenBurnsFallback: number;
}> = ({ backdrops, currentMs, kenBurnsFallback }) => {
  return (
    <AbsoluteFill>
      {backdrops.map((b, i) => {
        const visible = currentMs >= b.start_ms - 50 && currentMs < b.end_ms + BACKDROP_XFADE_MS;
        if (!visible || !b.photo_url) return null;
        const op =
          i === 0
            ? 1
            : interpolate(currentMs, [b.start_ms, b.start_ms + BACKDROP_XFADE_MS], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              });
        const span = Math.max(1, b.end_ms - b.start_ms);
        const t = Math.max(0, Math.min(1, (currentMs - b.start_ms) / span));
        const scale = backdrops.length === 1 ? kenBurnsFallback : 1.02 + 0.07 * t;
        const panX = (i % 2 === 0 ? -1 : 1) * 1.2 * t;
        return (
          <AbsoluteFill key={i} style={{ opacity: op }}>
            <Img
              src={b.photo_url}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                transform: `scale(${scale}) translateX(${panX}%)`,
                transformOrigin: "center",
              }}
            />
          </AbsoluteFill>
        );
      })}
    </AbsoluteFill>
  );
};

const BrollCut: React.FC<{ beat: BeatEntry; currentMs: number; index: number }> = ({
  beat,
  currentMs,
  index,
}) => {
  const span = Math.max(1, beat.hide_ms - beat.show_ms);
  const t = Math.max(0, Math.min(1, (currentMs - beat.show_ms) / span));
  const dir = index % 2 === 0 ? 1 : -1;
  const scale = 1.05 + 0.09 * t;
  const panX = dir * (2.2 * t - 1.1);
  return (
    <AbsoluteFill style={{ opacity: beatOpacity(beat, currentMs) }}>
      <Img
        src={beat.photo_url as string}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `scale(${scale}) translateX(${panX}%)`,
          transformOrigin: "center",
        }}
      />
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(180deg, rgba(0,0,0,0.18) 0%, rgba(0,0,0,0) 30%, rgba(0,0,0,0.12) 60%, rgba(0,0,0,0.55) 100%)",
        }}
      />
    </AbsoluteFill>
  );
};

const BeatsLayer: React.FC<{
  beats: BeatEntry[];
  currentMs: number;
  fallbackColor: string;
}> = ({ beats, currentMs, fallbackColor }) => {
  const nodes: React.ReactNode[] = [];
  beats.forEach((b, i) => {
    // Render with a little slack either side so fades complete.
    if (currentMs < b.show_ms || currentMs >= b.hide_ms) return;
    const localMs = currentMs - b.show_ms;
    const durationMs = Math.max(1, b.hide_ms - b.show_ms);
    const color = b.speakerColor || fallbackColor;
    const op = beatOpacity(b, currentMs);
    if (b.type === "broll") {
      if (b.photo_url) nodes.push(<BrollCut key={`b${i}`} beat={b} currentMs={currentMs} index={i} />);
      return;
    }
    const stage: React.CSSProperties = {
      position: "absolute",
      top: 90,
      left: 70,
      right: 70,
      bottom: 330,
      pointerEvents: "none",
    };
    const progress = Math.max(0, Math.min(1, localMs / durationMs));
    let inner: React.ReactNode = null;
    if (b.type === "chapter") {
      nodes.push(
        <AbsoluteFill key={`c${i}`} style={{ opacity: op }}>
          <ChapterTitleOverlay
            title={b.title || ""}
            subtitle={b.subtitle}
            chapterNumber={b.chapter_number}
            accent={b.accent || color}
            localMs={localMs}
            durationMs={durationMs}
          />
        </AbsoluteFill>,
      );
      return;
    }
    if (b.type === "stat") {
      inner = (
        <StatHero
          label={b.label || ""}
          value={b.value || ""}
          subtext={b.subtext}
          companions={b.companions}
          localMs={localMs}
          durationMs={durationMs}
          speakerColor={color}
          landMs={b.land_ms !== undefined ? b.land_ms - b.show_ms : undefined}
          countUp={b.count_up !== undefined ? b.count_up : b.static ? false : undefined}
        />
      );
    } else if (b.type === "diagram") {
      inner = (
        <DiagramOverlay
          kind={b.kind || "pillars"}
          title={b.title}
          nodes={b.nodes}
          center={b.center}
          columns={b.columns}
          quadrant={b.quadrant}
          localMs={localMs}
          durationMs={durationMs}
          speakerColor={color}
        />
      );
    } else if (b.type === "bullets") {
      inner = (
        <BulletOverlay title={b.title || ""} items={b.items || []} progress={progress} speakerColor={color} />
      );
    } else if (b.type === "chart") {
      inner = (
        <ChartOverlay
          title={b.title || ""}
          data_points={b.data_points || []}
          chart_type={b.chart_type || "growth"}
          progress={progress}
          speakerColor={color}
        />
      );
    }
    if (inner) {
      nodes.push(
        <AbsoluteFill key={`k${i}`} style={{ opacity: op }}>
          {/* readability scrim under cards */}
          <AbsoluteFill style={{ backgroundColor: "rgba(6,6,18,0.5)" }} />
          <div style={stage}>{inner}</div>
        </AbsoluteFill>,
      );
    }
  });
  return <>{nodes}</>;
};
