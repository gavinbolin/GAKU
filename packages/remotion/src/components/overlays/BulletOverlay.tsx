import React from "react";
import { interpolate } from "remotion";

export interface BulletOverlayProps {
  title: string;
  items: string[];
  progress: number;
  speakerColor: string;
}

export const BulletOverlay: React.FC<BulletOverlayProps> = ({
  title,
  items,
  progress,
  speakerColor,
}) => {
  return (
    <div
      style={{
        position: "absolute",
        top: "50%",
        left: "50%",
        transform: "translate(-50%, -50%)",
        width: "min(700px, 70%)",
        maxWidth: 700,
        padding: "36px 44px",
        borderRadius: 20,
        backgroundColor: "rgba(0,0,0,0.6)",
        backdropFilter: "blur(8px)",
        WebkitBackdropFilter: "blur(8px)",
        border: "1px solid rgba(255,255,255,0.15)",
        boxShadow: "0 20px 60px rgba(0,0,0,0.55)",
        fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
        color: "#f5f0e8",
        boxSizing: "border-box",
      }}
    >
      {title ? (
        <div
          style={{
            fontSize: 28,
            fontWeight: 800,
            color: speakerColor,
            textTransform: "uppercase",
            letterSpacing: 3,
            marginBottom: 22,
          }}
        >
          {title}
        </div>
      ) : null}
      <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        {items.map((item, i) => {
          const threshold = i * 0.15;
          const local = interpolate(progress, [threshold, threshold + 0.18], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          const opacity = local;
          const translate = interpolate(local, [0, 1], [16, 0]);
          return (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 18,
                opacity,
                transform: `translateX(${translate}px)`,
              }}
            >
              <div
                style={{
                  width: 14,
                  height: 14,
                  borderRadius: 7,
                  backgroundColor: speakerColor,
                  boxShadow: `0 0 12px ${speakerColor}`,
                  flexShrink: 0,
                }}
              />
              <div style={{ fontSize: 30, fontWeight: 500, lineHeight: 1.3 }}>
                {item}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
