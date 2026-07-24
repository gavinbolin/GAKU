import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface GrowthCurvePoint {
  x: number;
  y: number;
}

// Loose input shape: a Gemini-generated script may emit string x values, or
// use `dataPoints` instead of `points`. We coerce defensively at render time.
type LooseGrowthCurvePoint = {
  x?: number | string;
  y?: number | string;
};

export interface GrowthCurveProps {
  label: string;
  points?: LooseGrowthCurvePoint[];
  dataPoints?: LooseGrowthCurvePoint[];
  y_axis_label?: string;
  x_axis_label?: string;
}

function coercePoints(
  points: LooseGrowthCurvePoint[] | undefined,
  dataPoints: LooseGrowthCurvePoint[] | undefined
): GrowthCurvePoint[] {
  const src = points && points.length > 0 ? points : dataPoints;
  if (!src) {
    console.warn("[GrowthCurve] no `points` or `dataPoints` provided");
    return [];
  }
  return src.map((p, i) => {
    let x: number;
    const rawX = p?.x;
    if (typeof rawX === "number" && Number.isFinite(rawX)) {
      x = rawX;
    } else if (typeof rawX === "string") {
      const parsed = Number(rawX);
      if (Number.isFinite(parsed)) {
        x = parsed;
      } else {
        console.warn(
          `[GrowthCurve] point[${i}].x is non-numeric string ${JSON.stringify(rawX)} — falling back to index ${i}`
        );
        x = i;
      }
    } else {
      console.warn(
        `[GrowthCurve] point[${i}].x missing/invalid — falling back to index ${i}`
      );
      x = i;
    }
    const rawY = p?.y;
    const y =
      typeof rawY === "number"
        ? rawY
        : typeof rawY === "string"
        ? Number(rawY) || 0
        : 0;
    return { x, y };
  });
}

const BG = "#1a1a2e";
const TEXT = "#f5f0e8";
const ACCENT = "#7C3AED";

const PADDING = 120;
const PLOT_W = 1680;
const PLOT_H = 760;

export const GrowthCurve: React.FC<GrowthCurveProps> = ({
  label,
  points,
  dataPoints,
  y_axis_label,
  x_axis_label,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const drawProgress = interpolate(
    frame,
    [10, Math.max(11, durationInFrames - 20)],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const labelOpacity = interpolate(frame, [0, 18], [0, 1], {
    extrapolateRight: "clamp",
  });

  const coerced = React.useMemo(
    () => coercePoints(points, dataPoints),
    [points, dataPoints]
  );

  const { d, length } = React.useMemo(
    () => buildPath(coerced, PLOT_W, PLOT_H),
    [coerced]
  );
  const dashOffset = length * (1 - drawProgress);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: BG,
        color: TEXT,
        fontFamily: "Georgia, 'Times New Roman', serif",
        padding: PADDING,
      }}
    >
      <div style={{ fontSize: 56, fontWeight: 700, opacity: labelOpacity }}>
        {label}
      </div>

      <svg
        width={PLOT_W}
        height={PLOT_H}
        style={{ marginTop: 24 }}
        viewBox={`0 0 ${PLOT_W} ${PLOT_H}`}
      >
        <line
          x1={60}
          y1={PLOT_H - 60}
          x2={PLOT_W - 20}
          y2={PLOT_H - 60}
          stroke={TEXT}
          strokeOpacity={0.4}
          strokeWidth={2}
        />
        <line
          x1={60}
          y1={20}
          x2={60}
          y2={PLOT_H - 60}
          stroke={TEXT}
          strokeOpacity={0.4}
          strokeWidth={2}
        />

        <path
          d={d}
          stroke={ACCENT}
          strokeWidth={6}
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeDasharray={length}
          strokeDashoffset={dashOffset}
        />
      </svg>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          marginTop: 16,
          opacity: 0.7,
          fontSize: 28,
        }}
      >
        <span>{x_axis_label ?? ""}</span>
        <span>{y_axis_label ?? ""}</span>
      </div>
    </AbsoluteFill>
  );
};

function buildPath(
  points: GrowthCurvePoint[],
  width: number,
  height: number
): { d: string; length: number } {
  if (points.length === 0) return { d: "", length: 0 };

  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMin = Math.min(...ys);
  const yMax = Math.max(...ys);
  const xRange = xMax - xMin || 1;
  const yRange = yMax - yMin || 1;

  const left = 60;
  const right = width - 20;
  const top = 20;
  const bottom = height - 60;

  const projected = points.map((p) => {
    const px = left + ((p.x - xMin) / xRange) * (right - left);
    const py = bottom - ((p.y - yMin) / yRange) * (bottom - top);
    return [px, py] as const;
  });

  const d = projected
    .map(([x, y], i) => (i === 0 ? `M ${x} ${y}` : `L ${x} ${y}`))
    .join(" ");

  let length = 0;
  for (let i = 1; i < projected.length; i++) {
    const [x0, y0] = projected[i - 1];
    const [x1, y1] = projected[i];
    length += Math.hypot(x1 - x0, y1 - y0);
  }

  return { d, length };
}
