import React from "react";
import { interpolate } from "remotion";

/**
 * ChapterTitleOverlay -- mid-episode chapter card (2026-09-25 item 4).
 * TitleCard look (serif title, glowing kicker, accent underline) rendered
 * INSIDE the SegmentCard so karaoke captions keep running underneath.
 */
export interface ChapterTitleOverlayProps {
  title: string;
  subtitle?: string;
  chapterNumber?: number;
  accent: string;
  localMs: number;
  durationMs: number;
}

export const ChapterTitleOverlay: React.FC<ChapterTitleOverlayProps> = ({
  title,
  subtitle,
  chapterNumber,
  accent,
  localMs,
}) => {
  const kickerIn = interpolate(localMs, [0, 400], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const titleIn = interpolate(localMs, [150, 650], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const subIn = interpolate(localMs, [550, 1050], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const line = interpolate(localMs, [700, 1400], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const kicker = chapterNumber ? `Chapter ${String(chapterNumber).padStart(2, "0")}` : "Next up";

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background:
          "linear-gradient(90deg, rgba(8,8,22,0.86) 0%, rgba(8,8,22,0.62) 55%, rgba(8,8,22,0.25) 100%)",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        padding: "0 140px 260px 140px",
        boxSizing: "border-box",
        fontFamily: "Georgia, 'Times New Roman', serif",
        color: "#f5f0e8",
      }}
    >
      <div
        style={{
          fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
          fontSize: 28,
          fontWeight: 800,
          letterSpacing: 9,
          textTransform: "uppercase",
          color: accent,
          opacity: kickerIn,
          transform: `translateY(${(1 - kickerIn) * -12}px)`,
          textShadow: `0 0 22px ${accent}88`,
          marginBottom: 22,
        }}
      >
        {kicker}
      </div>
      <div
        style={{
          fontSize: title.length > 34 ? 88 : 108,
          fontWeight: 700,
          lineHeight: 1.04,
          maxWidth: 1500,
          opacity: titleIn,
          transform: `translateY(${(1 - titleIn) * 48}px)`,
          textShadow: "0 10px 40px rgba(0,0,0,0.5)",
        }}
      >
        {title}
      </div>
      <div
        style={{
          marginTop: 30,
          height: 6,
          width: 300 * line,
          backgroundColor: accent,
          borderRadius: 4,
          boxShadow: `0 0 18px ${accent}`,
        }}
      />
      {subtitle ? (
        <div
          style={{
            marginTop: 28,
            fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
            fontSize: 40,
            fontWeight: 600,
            color: "rgba(245,240,232,0.9)",
            maxWidth: 1400,
            opacity: subIn,
            transform: `translateY(${(1 - subIn) * 16}px)`,
          }}
        >
          {subtitle}
        </div>
      ) : null}
    </div>
  );
};
