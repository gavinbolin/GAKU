import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface PhotoCardProps {
  photo_url?: string;
  caption?: string;
  photographer?: string;
  overlay_opacity?: number;
}

const TEXT = "#f5f0e8";
const ACCENT = "#7C3AED";

export const PhotoCard: React.FC<PhotoCardProps> = ({
  photo_url,
  caption,
  photographer,
  overlay_opacity = 0.5,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  // Ken Burns: scale 1.00 -> 1.08 over duration
  const scale = interpolate(
    frame,
    [0, durationInFrames - 1],
    [1.0, 1.08],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const captionOpacity = interpolate(frame, [10, 32], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const captionY = interpolate(frame, [10, 32], [16, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const hasPhoto = typeof photo_url === "string" && photo_url.trim().length > 0;

  if (!hasPhoto) {
    return (
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(135deg, #1a0b3d 0%, #2d1b69 45%, #0f2a5e 100%)",
          justifyContent: "center",
          alignItems: "center",
          padding: "0 160px",
        }}
      >
        <div
          style={{
            width: 80,
            height: 4,
            backgroundColor: ACCENT,
            marginBottom: 36,
            opacity: captionOpacity,
          }}
        />
        <div
          style={{
            fontFamily: "Georgia, 'Times New Roman', serif",
            fontSize: 72,
            fontWeight: 700,
            color: TEXT,
            lineHeight: 1.2,
            textAlign: "center",
            maxWidth: 1500,
            textShadow: "0 4px 20px rgba(0,0,0,0.5)",
            opacity: captionOpacity,
            transform: `translateY(${captionY}px)`,
          }}
        >
          {caption || ""}
        </div>
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={{ backgroundColor: "#0f0f1f", overflow: "hidden" }}>
      <AbsoluteFill
        style={{
          transform: `scale(${scale})`,
          transformOrigin: "center",
        }}
      >
        <Img
          src={photo_url}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
          }}
        />
      </AbsoluteFill>

      <AbsoluteFill
        style={{
          background: `linear-gradient(180deg, rgba(15,15,31,${overlay_opacity * 0.4}) 0%, rgba(15,15,31,${overlay_opacity * 0.2}) 40%, rgba(15,15,31,${Math.min(0.95, overlay_opacity + 0.35)}) 100%)`,
        }}
      />

      {caption ? (
        <AbsoluteFill
          style={{
            justifyContent: "flex-end",
            alignItems: "flex-start",
            padding: "0 120px 140px 120px",
            opacity: captionOpacity,
            transform: `translateY(${captionY}px)`,
          }}
        >
          <div
            style={{
              width: 80,
              height: 4,
              backgroundColor: ACCENT,
              marginBottom: 28,
            }}
          />
          <div
            style={{
              fontFamily: "Georgia, 'Times New Roman', serif",
              fontSize: 64,
              fontWeight: 700,
              color: TEXT,
              lineHeight: 1.15,
              maxWidth: 1500,
              textShadow: "0 4px 18px rgba(0,0,0,0.6)",
            }}
          >
            {caption}
          </div>
        </AbsoluteFill>
      ) : null}

      {photographer ? (
        <div
          style={{
            position: "absolute",
            right: 36,
            bottom: 24,
            fontFamily: "system-ui, -apple-system, sans-serif",
            fontSize: 14,
            color: "rgba(245, 240, 232, 0.7)",
            letterSpacing: 2,
            textTransform: "uppercase",
            textShadow: "0 2px 6px rgba(0,0,0,0.6)",
          }}
        >
          Photo: {photographer}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};
