import React from "react";
import { Composition, registerRoot } from "remotion";

import { TitleCard } from "./templates/TitleCard";
import { GrowthCurve } from "./templates/GrowthCurve";
import { StatCallout } from "./templates/StatCallout";
import { BookCover } from "./templates/BookCover";
import { ChapterCard } from "./templates/ChapterCard";
import { FlowDiagram } from "./templates/FlowDiagram";
import { PodcastBackground } from "./templates/PodcastBackground";
import { SpeakerCard } from "./templates/SpeakerCard";
import { PullQuote } from "./templates/PullQuote";
import { KeyTermDefinition } from "./templates/KeyTermDefinition";
import { StatAnimation } from "./templates/StatAnimation";
import { ConceptComparison } from "./templates/ConceptComparison";
import { TimelineVisual } from "./templates/TimelineVisual";
import { PhotoCard } from "./templates/PhotoCard";
import { PercentageRing } from "./templates/PercentageRing";
import { SegmentCard } from "./templates/SegmentCard";

const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;
const DEFAULT_DURATION = 180; // 6 s @ 30fps; render_visual passes durationInFrames per call.

// render_visual injects `durationInFrames` into inputProps. This callback
// reads it back so each Composition's runtime length matches the request.
// Without this, all renders snap to the static `durationInFrames` prop on
// the Composition tag and ignore --props.
const durationFromProps = ({ props }: { props: Record<string, unknown> }) => {
  const raw = props.durationInFrames;
  const dur = typeof raw === "number" && raw > 0 ? Math.round(raw) : DEFAULT_DURATION;
  return { durationInFrames: dur };
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="TitleCard"
        component={TitleCard}
        calculateMetadata={durationFromProps}
        durationInFrames={DEFAULT_DURATION}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          title: "Sample Title",
          subtitle: "PROJECT GAKU",
        }}
      />
      <Composition
        id="GrowthCurve"
        component={GrowthCurve}
        calculateMetadata={durationFromProps}
        durationInFrames={DEFAULT_DURATION}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          label: "Sample Growth",
          points: [
            { x: 0, y: 1 },
            { x: 1, y: 2 },
            { x: 2, y: 4 },
            { x: 3, y: 8 },
            { x: 4, y: 16 },
          ],
          x_axis_label: "Time",
          y_axis_label: "Value",
        }}
      />
      <Composition
        id="StatCallout"
        component={StatCallout}
        calculateMetadata={durationFromProps}
        durationInFrames={DEFAULT_DURATION}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          value: 42,
          unit: "%",
          caption: "Sample stat caption.",
        }}
      />
      <Composition
        id="BookCover"
        component={BookCover}
        calculateMetadata={durationFromProps}
        durationInFrames={FPS * 5}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          title: "Sample Book Title",
          author: "Author Name",
        }}
      />
      <Composition
        id="ChapterCard"
        component={ChapterCard}
        calculateMetadata={durationFromProps}
        durationInFrames={FPS * 3}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          chapter_num: 1,
          chapter_title: "Introduction",
        }}
      />
      <Composition
        id="FlowDiagram"
        component={FlowDiagram}
        calculateMetadata={durationFromProps}
        durationInFrames={FPS * 6}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          title: "Sample Process",
          steps: [
            { label: "Plan", description: "Define the goal" },
            { label: "Build", description: "Make the thing" },
            { label: "Ship", description: "Get it in users' hands" },
          ],
          theme: "dark" as const,
        }}
      />
      <Composition
        id="PodcastBackground"
        component={PodcastBackground}
        calculateMetadata={durationFromProps}
        // 8s loop — long enough that the pseudo-waveform doesn't repeat
        // obviously, short enough that ffmpeg loop overhead is trivial.
        durationInFrames={FPS * 8}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          topic: "Sample Podcast Topic",
          subtitle: "PROJECT GAKU",
        }}
      />
      <Composition
        id="SpeakerCard"
        component={SpeakerCard}
        calculateMetadata={durationFromProps}
        durationInFrames={300}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          host_name: "George",
          host_role: "primary" as const,
          color: "#7C3AED",
          show_waveform: true,
        }}
      />
      <Composition
        id="PullQuote"
        component={PullQuote}
        calculateMetadata={durationFromProps}
        durationInFrames={FPS * 6}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          quote: "The most important things are the hardest to say.",
          speaker: "Stephen King",
          theme: "dark" as const,
        }}
      />
      <Composition
        id="KeyTermDefinition"
        component={KeyTermDefinition}
        calculateMetadata={durationFromProps}
        durationInFrames={FPS * 7}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          term: "Entropy",
          definition:
            "The measure of disorder or randomness in a closed system.",
          etymology: "From the Greek 'en' (in) + 'tropē' (transformation).",
          category: "Physics",
        }}
      />
      <Composition
        id="StatAnimation"
        component={StatAnimation}
        calculateMetadata={durationFromProps}
        durationInFrames={FPS * 6}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          value: 87,
          unit: "%",
          caption: "of users prefer this approach.",
          source: "Internal study, 2025",
          animate_type: "counter" as const,
        }}
      />
      <Composition
        id="ConceptComparison"
        component={ConceptComparison}
        calculateMetadata={durationFromProps}
        durationInFrames={FPS * 8}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          title: "Synchronous vs Asynchronous",
          left: {
            label: "Synchronous",
            points: [
              "Blocks the caller",
              "Simple to reason about",
              "Limited throughput",
            ],
          },
          right: {
            label: "Asynchronous",
            points: [
              "Non-blocking",
              "Higher complexity",
              "Scales horizontally",
            ],
          },
        }}
      />
      <Composition
        id="TimelineVisual"
        component={TimelineVisual}
        calculateMetadata={durationFromProps}
        durationInFrames={FPS * 8}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          title: "A Brief History",
          events: [
            { year: "1969", label: "ARPANET" },
            { year: "1983", label: "TCP/IP" },
            { year: "1991", label: "WWW" },
            { year: "2007", label: "iPhone" },
          ],
        }}
      />
      <Composition
        id="PhotoCard"
        component={PhotoCard}
        calculateMetadata={durationFromProps}
        durationInFrames={FPS * 6}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          photo_url:
            "https://images.pexels.com/photos/1133957/pexels-photo-1133957.jpeg",
          caption: "Sample caption text.",
          photographer: "Pexels",
          overlay_opacity: 0.5,
        }}
      />
      <Composition
        id="PercentageRing"
        component={PercentageRing}
        calculateMetadata={durationFromProps}
        durationInFrames={FPS * 6}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          percentage: 72,
          label: "of the system is covered.",
          color: "#7C3AED",
          show_remaining: true,
        }}
      />
      <Composition
        id="SegmentCard"
        component={SegmentCard}
        calculateMetadata={durationFromProps}
        durationInFrames={300}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          title: "What exactly is App Development?",
          photo_url: undefined,
          host_a_name: "Alex",
          host_b_name: "Jordan",
          host_a_color: "#7C3AED",
          host_b_color: "#0EA5E9",
          current_speaker: "both" as const,
          segment_number: 1,
          sentence_timings: [],
          graphics: [],
        }}
      />
    </>
  );
};

registerRoot(RemotionRoot);
