export type TemplateName =
  | "TitleCard"
  | "GrowthCurve"
  | "StatCallout"
  | "BookCover"
  | "ChapterCard"
  | "FlowDiagram";

export interface TitleCardData {
  title: string;
  subtitle?: string;
}

export interface GrowthCurvePoint {
  x: number;
  y: number;
}

export interface GrowthCurveData {
  label?: string;
  points: GrowthCurvePoint[];
  x_axis_label?: string;
  y_axis_label?: string;
}

export interface StatCalloutData {
  value: number;
  unit?: string;
  caption: string;
}

export interface BookCoverData {
  title: string;
  author: string;
}

export interface ChapterCardData {
  chapter_num: number;
  chapter_title: string;
}

export interface FlowDiagramStep {
  label: string;
  description?: string;
}

export interface FlowDiagramData {
  steps: FlowDiagramStep[];
  title?: string;
  active_step?: number;
  theme?: "light" | "dark";
}

export type VisualData =
  | TitleCardData
  | GrowthCurveData
  | StatCalloutData
  | BookCoverData
  | ChapterCardData
  | FlowDiagramData
  | Record<string, unknown>;

export interface Host {
  id: string;
  name: string;
  voice_id: string;
}

export interface Line {
  line_id: string;
  host_id: string;
  text: string;
  pause_after_ms?: number;
}

export type CueMode = "after_speech" | "after_pause";

export interface Visual {
  visual_id: string;
  template: TemplateName;
  // New canonical field — anchors the visual to the START of the referenced line.
  start_with_line?: string;
  // Deprecated alias kept for backward compat with older episode JSONs. When
  // only this is set, the visual is anchored to the END of the line (legacy
  // semantics) and cue_mode determines whether the trailing pause counts.
  start_after_line?: string;
  duration_ms: number;
  cue_mode?: CueMode;
  data: VisualData;
}

export interface Segment {
  segment_id: string;
  title: string;
  lines: Line[];
  visuals?: Visual[];
}

export interface Episode {
  episode_id: string;
  topic: string;
  target_duration_minutes: number;
  hosts: [Host, Host];
  segments: Segment[];
}
