import React from "react";
import { interpolate } from "remotion";

export interface ChartDataPoint {
  label: string;
  value: number;
}

export interface ChartOverlayProps {
  title: string;
  data_points: ChartDataPoint[];
  chart_type: string;
  progress: number;
  speakerColor: string;
}

export const ChartOverlay: React.FC<ChartOverlayProps> = ({
  title,
  data_points,
  progress,
  speakerColor,
}) => {
  const points = data_points || [];
  const maxValue = points.reduce((m, p) => Math.max(m, p.value), 0) || 1;
  const n = points.length || 1;

  const chartHeight = 320;
  const chartGap = 18;

  return (
    <div
      style={{
        position: "absolute",
        top: "50%",
        left: "50%",
        transform: "translate(-50%, -50%)",
        width: "min(800px, 75%)",
        maxWidth: 800,
        padding: "32px 40px 28px",
        borderRadius: 22,
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
            fontSize: 26,
            fontWeight: 800,
            color: speakerColor,
            textTransform: "uppercase",
            letterSpacing: 3,
            marginBottom: 22,
            textAlign: "center",
          }}
        >
          {title}
        </div>
      ) : null}

      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          gap: chartGap,
          height: chartHeight,
        }}
      >
        {points.map((p, i) => {
          // Stagger the bar reveal left-to-right across the first 70% of the
          // overlay window. Last bar fully reveals around progress=0.85.
          const threshold = (i / Math.max(1, n)) * 0.7;
          const local = interpolate(
            progress,
            [threshold, threshold + 0.3],
            [0, 1],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
          );
          const targetFrac = p.value / maxValue;
          const barH = chartHeight * targetFrac * local;
          return (
            <div
              key={i}
              style={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 10,
                height: "100%",
                justifyContent: "flex-end",
                minWidth: 0,
              }}
            >
              <div
                style={{
                  fontSize: 18,
                  fontWeight: 700,
                  color: "rgba(245,240,232,0.85)",
                  opacity: local,
                }}
              >
                {p.value}
              </div>
              <div
                style={{
                  width: "100%",
                  height: barH,
                  borderRadius: 8,
                  background: `linear-gradient(to bottom, ${speakerColor}, ${speakerColor}66)`,
                  boxShadow: `0 0 18px ${speakerColor}55`,
                }}
              />
            </div>
          );
        })}
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: chartGap,
          marginTop: 12,
          borderTop: "1px solid rgba(255,255,255,0.15)",
          paddingTop: 12,
        }}
      >
        {points.map((p, i) => (
          <div
            key={i}
            style={{
              flex: 1,
              fontSize: 18,
              fontWeight: 500,
              color: "rgba(245,240,232,0.75)",
              textAlign: "center",
              minWidth: 0,
            }}
          >
            {p.label}
          </div>
        ))}
      </div>
    </div>
  );
};
