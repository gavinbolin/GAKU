export type TemplateName = "TitleCard" | "GrowthCurve" | "StatCallout" | "BookCover" | "ChapterCard" | "FlowDiagram";
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
export type VisualData = TitleCardData | GrowthCurveData | StatCalloutData | BookCoverData | ChapterCardData | FlowDiagramData | Record<string, unknown>;
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
    start_with_line?: string;
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
