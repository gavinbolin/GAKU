import type { ComponentType } from "react";

import { TitleCard } from "./templates/TitleCard";
import { GrowthCurve } from "./templates/GrowthCurve";
import { StatCallout } from "./templates/StatCallout";
import { BookCover } from "./templates/BookCover";
import { ChapterCard } from "./templates/ChapterCard";
import { FlowDiagram } from "./templates/FlowDiagram";
import { SpeakerCard } from "./templates/SpeakerCard";
import { PullQuote } from "./templates/PullQuote";
import { KeyTermDefinition } from "./templates/KeyTermDefinition";
import { StatAnimation } from "./templates/StatAnimation";
import { ConceptComparison } from "./templates/ConceptComparison";
import { TimelineVisual } from "./templates/TimelineVisual";
import { PhotoCard } from "./templates/PhotoCard";
import { PercentageRing } from "./templates/PercentageRing";

export type TemplateName =
  | "TitleCard"
  | "GrowthCurve"
  | "StatCallout"
  | "BookCover"
  | "ChapterCard"
  | "FlowDiagram"
  | "SpeakerCard"
  | "PullQuote"
  | "KeyTermDefinition"
  | "StatAnimation"
  | "ConceptComparison"
  | "TimelineVisual"
  | "PhotoCard"
  | "PercentageRing";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const templateRegistry: Record<TemplateName, ComponentType<any>> = {
  TitleCard,
  GrowthCurve,
  StatCallout,
  BookCover,
  ChapterCard,
  FlowDiagram,
  SpeakerCard,
  PullQuote,
  KeyTermDefinition,
  StatAnimation,
  ConceptComparison,
  TimelineVisual,
  PhotoCard,
  PercentageRing,
};
