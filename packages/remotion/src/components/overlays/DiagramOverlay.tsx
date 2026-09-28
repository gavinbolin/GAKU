import React from "react";
import { interpolate } from "remotion";

/**
 * DiagramOverlay -- Remotion-native diagrams driven by the script
 * (2026-09-25 item 5; in the spirit of the Agentforce scaling card).
 *
 * kinds:
 *   flow        numbered process steps with arrows (data-readiness steps)
 *   hub         shared goal in the center, roles as spokes (collaboration model)
 *   pillars     labeled columns on a foundation (framework / companies / takeaways)
 *   comparison  two columns with a VS badge
 *   timeline    dated milestones on an axis
 *   definition  key term card
 *   quote       pull quote
 *   quadrant    2x2 strategy matrix: two labeled axes, four labeled cells,
 *               optional highlighted cell (2026-09-28 item 2)
 */

export interface DiagramNode {
  label: string;
  detail?: string;
}

export interface DiagramColumn {
  label: string;
  points: string[];
}

export interface QuadrantAxis {
  label: string;
  low?: string;
  high?: string;
}

export interface QuadrantCell {
  label: string;
  detail?: string;
  /** 0 = low / left, 1 = high / right */
  x: number;
  /** 0 = low / bottom, 1 = high / top */
  y: number;
}

export interface QuadrantData {
  x_axis: QuadrantAxis;
  y_axis: QuadrantAxis;
  cells: QuadrantCell[];
  /** index into cells to spotlight (others dim); null/undefined = all equal */
  highlight?: number | null;
}

export interface DiagramOverlayProps {
  kind: string;
  quadrant?: QuadrantData;
  title?: string;
  nodes?: DiagramNode[];
  center?: string;
  columns?: DiagramColumn[];
  localMs: number;
  durationMs: number;
  speakerColor: string;
}

const TEXT = "#f5f0e8";
const MUTED = "rgba(245,240,232,0.72)";
const PANEL = "linear-gradient(180deg, rgba(20,20,44,0.92) 0%, rgba(10,10,26,0.9) 100%)";
const FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif";

const reveal = (localMs: number, i: number, n: number, durationMs: number): number => {
  const window = Math.min(durationMs * 0.55, 900 + n * 900);
  const start = 250 + (window / Math.max(1, n)) * i;
  return interpolate(localMs, [start, start + 450], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
};

const Title: React.FC<{ text?: string; color: string; localMs: number }> = ({ text, color, localMs }) =>
  text ? (
    <div
      style={{
        fontSize: 34,
        fontWeight: 800,
        letterSpacing: 5,
        textTransform: "uppercase",
        color,
        textAlign: "center",
        marginBottom: 30,
        opacity: interpolate(localMs, [0, 300], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
        textShadow: `0 0 24px ${color}55`,
      }}
    >
      {text}
    </div>
  ) : null;

const Flow: React.FC<DiagramOverlayProps> = ({ nodes = [], localMs, durationMs, speakerColor }) => {
  const n = nodes.length;
  return (
    <div style={{ display: "flex", alignItems: "stretch", justifyContent: "center", gap: 0 }}>
      {nodes.map((nd, i) => {
        const r = reveal(localMs, i, n, durationMs);
        const arrow = i < n - 1 ? reveal(localMs, i + 0.6, n, durationMs) : 0;
        return (
          <React.Fragment key={i}>
            <div
              style={{
                width: n >= 5 ? 280 : 330,
                padding: "30px 26px",
                borderRadius: 22,
                background: PANEL,
                border: `2px solid ${speakerColor}aa`,
                boxShadow: `0 0 26px ${speakerColor}33, 0 18px 40px rgba(0,0,0,0.45)`,
                opacity: r,
                transform: `translateY(${(1 - r) * 30}px)`,
                display: "flex",
                flexDirection: "column",
                gap: 14,
              }}
            >
              <div
                style={{
                  width: 58,
                  height: 58,
                  borderRadius: 29,
                  backgroundColor: speakerColor,
                  color: "#0a0a16",
                  fontSize: 30,
                  fontWeight: 900,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  boxShadow: `0 0 20px ${speakerColor}`,
                }}
              >
                {i + 1}
              </div>
              <div style={{ fontSize: 31, fontWeight: 800, lineHeight: 1.2, color: TEXT }}>{nd.label}</div>
              {nd.detail ? <div style={{ fontSize: 23, color: MUTED, lineHeight: 1.3 }}>{nd.detail}</div> : null}
            </div>
            {i < n - 1 ? (
              <div style={{ width: 70, display: "flex", alignItems: "center", justifyContent: "center" }}>
                <svg width={62} height={40} viewBox="0 0 62 40" style={{ opacity: arrow }}>
                  <line x1={2} y1={20} x2={2 + 46 * arrow} y2={20} stroke={speakerColor} strokeWidth={6} strokeLinecap="round" />
                  <polygon points="44,6 60,20 44,34" fill={speakerColor} opacity={arrow > 0.9 ? 1 : 0} />
                </svg>
              </div>
            ) : null}
          </React.Fragment>
        );
      })}
    </div>
  );
};

const Hub: React.FC<DiagramOverlayProps> = ({ nodes = [], center, title, localMs, durationMs, speakerColor }) => {
  const W = 1500;
  const H = 540;
  const cx = W / 2;
  const cy = H / 2;
  const n = nodes.length;
  const rx = 540;
  const ry = 195;
  const pts = nodes.map((_, i) => {
    const a = -Math.PI / 2 + (i / n) * Math.PI * 2 + (n === 2 ? Math.PI / 2 : 0);
    return { x: cx + rx * Math.cos(a), y: cy + ry * Math.sin(a) };
  });
  const cIn = interpolate(localMs, [150, 650], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ position: "relative", width: W, height: H, margin: "0 auto" }}>
      <svg width={W} height={H} style={{ position: "absolute", inset: 0 }}>
        {pts.map((p, i) => {
          const r = reveal(localMs, i, n, durationMs);
          return (
            <line
              key={i}
              x1={cx}
              y1={cy}
              x2={cx + (p.x - cx) * r}
              y2={cy + (p.y - cy) * r}
              stroke={speakerColor}
              strokeOpacity={0.75}
              strokeWidth={4}
              strokeDasharray="10 8"
            />
          );
        })}
      </svg>
      <div
        style={{
          position: "absolute",
          left: cx - 170,
          top: cy - 110,
          width: 340,
          height: 220,
          borderRadius: 110,
          background: `radial-gradient(circle at 50% 40%, ${speakerColor}ee 0%, ${speakerColor}99 70%)`,
          boxShadow: `0 0 60px ${speakerColor}88`,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          textAlign: "center",
          padding: 26,
          boxSizing: "border-box",
          color: "#0a0a16",
          opacity: cIn,
          transform: `scale(${0.8 + 0.2 * cIn})`,
        }}
      >
        <div style={{ fontSize: 17, fontWeight: 800, letterSpacing: 3, textTransform: "uppercase", opacity: 0.75 }}>
          Shared goal
        </div>
        <div style={{ fontSize: 32, fontWeight: 900, lineHeight: 1.12, marginTop: 6 }}>{center || title}</div>
      </div>
      {nodes.map((nd, i) => {
        const r = reveal(localMs, i + 0.5, n + 0.5, durationMs);
        const p = pts[i];
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: p.x - 190,
              top: p.y - 62,
              width: 380,
              minHeight: 124,
              padding: "20px 24px",
              boxSizing: "border-box",
              borderRadius: 20,
              background: PANEL,
              border: `2px solid ${speakerColor}99`,
              boxShadow: "0 16px 40px rgba(0,0,0,0.5)",
              opacity: r,
              transform: `scale(${0.85 + 0.15 * r})`,
              textAlign: "center",
            }}
          >
            <div style={{ fontSize: 29, fontWeight: 800, color: TEXT, lineHeight: 1.2 }}>{nd.label}</div>
            {nd.detail ? <div style={{ fontSize: 22, color: MUTED, marginTop: 8, lineHeight: 1.3 }}>{nd.detail}</div> : null}
          </div>
        );
      })}
    </div>
  );
};

const Pillars: React.FC<DiagramOverlayProps> = ({ nodes = [], localMs, durationMs, speakerColor }) => {
  const n = nodes.length;
  const w = n >= 5 ? 290 : n === 4 ? 340 : 400;
  const base = interpolate(localMs, [100, 600], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
      <div style={{ display: "flex", gap: 26, alignItems: "flex-end" }}>
        {nodes.map((nd, i) => {
          const r = reveal(localMs, i, n, durationMs);
          return (
            <div
              key={i}
              style={{
                width: w,
                minHeight: 250,
                padding: "30px 26px",
                boxSizing: "border-box",
                borderRadius: "22px 22px 6px 6px",
                background: PANEL,
                borderTop: `8px solid ${speakerColor}`,
                boxShadow: `0 0 26px ${speakerColor}22, 0 18px 40px rgba(0,0,0,0.45)`,
                opacity: r,
                transform: `translateY(${(1 - r) * 60}px)`,
                transformOrigin: "bottom center",
                display: "flex",
                flexDirection: "column",
                gap: 14,
              }}
            >
              <div style={{ fontSize: nd.detail ? 32 : 30, fontWeight: 800, color: nd.detail ? speakerColor : TEXT, lineHeight: 1.2 }}>
                {nd.label}
              </div>
              {nd.detail ? <div style={{ fontSize: 25, color: TEXT, lineHeight: 1.3 }}>{nd.detail}</div> : null}
            </div>
          );
        })}
      </div>
      <div
        style={{
          marginTop: 16,
          height: 18,
          width: `${Math.round(base * (n * (w + 26) + 30))}px`,
          borderRadius: 9,
          background: `linear-gradient(90deg, ${speakerColor}55, ${speakerColor}, ${speakerColor}55)`,
          boxShadow: `0 0 24px ${speakerColor}77`,
        }}
      />
    </div>
  );
};

const Comparison: React.FC<DiagramOverlayProps> = ({ columns = [], localMs, durationMs, speakerColor }) => {
  const cols = columns.slice(0, 2);
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 40 }}>
      {cols.map((c, ci) => {
        const r = reveal(localMs, ci, 2, durationMs);
        return (
          <React.Fragment key={ci}>
            {ci === 1 ? (
              <div
                style={{
                  width: 110,
                  height: 110,
                  borderRadius: 55,
                  backgroundColor: speakerColor,
                  color: "#0a0a16",
                  fontSize: 40,
                  fontWeight: 900,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  boxShadow: `0 0 34px ${speakerColor}`,
                }}
              >
                VS
              </div>
            ) : null}
            <div
              style={{
                width: 600,
                padding: "32px 36px",
                borderRadius: 24,
                background: PANEL,
                border: `2px solid ${speakerColor}88`,
                opacity: r,
                transform: `translateX(${(1 - r) * (ci === 0 ? -40 : 40)}px)`,
              }}
            >
              <div style={{ fontSize: 36, fontWeight: 900, color: speakerColor, marginBottom: 18 }}>{c.label}</div>
              {c.points.map((pt, pi) => (
                <div key={pi} style={{ fontSize: 28, color: TEXT, marginTop: pi ? 12 : 0, lineHeight: 1.3 }}>
                  • {pt}
                </div>
              ))}
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
};

const Timeline: React.FC<DiagramOverlayProps> = ({ nodes = [], localMs, durationMs, speakerColor }) => {
  const n = nodes.length;
  const line = interpolate(localMs, [100, 900], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ position: "relative", width: 1560, height: 360, margin: "0 auto" }}>
      <div
        style={{
          position: "absolute",
          top: 170,
          left: 40,
          height: 6,
          width: 1480 * line,
          backgroundColor: speakerColor,
          borderRadius: 3,
          boxShadow: `0 0 18px ${speakerColor}`,
        }}
      />
      {nodes.map((nd, i) => {
        const x = 40 + (1480 * (i + 0.5)) / n;
        const r = reveal(localMs, i, n, durationMs);
        return (
          <div key={i} style={{ position: "absolute", left: x - 150, top: 0, width: 300, textAlign: "center", opacity: r }}>
            <div style={{ fontSize: 40, fontWeight: 900, color: TEXT, height: 130, display: "flex", alignItems: "flex-end", justifyContent: "center" }}>
              {nd.label}
            </div>
            <div style={{ width: 30, height: 30, borderRadius: 15, background: speakerColor, margin: "22px auto", boxShadow: `0 0 18px ${speakerColor}` }} />
            <div style={{ fontSize: 24, color: MUTED, lineHeight: 1.3 }}>{nd.detail}</div>
          </div>
        );
      })}
    </div>
  );
};


const Quadrant: React.FC<DiagramOverlayProps> = ({ quadrant, localMs, durationMs, speakerColor }) => {
  if (!quadrant || !quadrant.cells || quadrant.cells.length !== 4) return null;
  const { x_axis, y_axis, cells } = quadrant;
  const hl = quadrant.highlight ?? null;
  // Compact enough that the whole matrix (incl. the bottom row + x-axis labels)
  // clears a 4-line karaoke caption block (stage ~ y 90..600 in 1080p).
  const CELL_W = 540;
  const CELL_H = 136;
  const GAP = 12;
  const GRID_W = CELL_W * 2 + GAP;
  const GRID_H = CELL_H * 2 + GAP;
  const TITLE_COL = 58; // rotated y-axis title (may wrap to 2 lines)
  const TICK_COL = 30; // rotated High / Low ticks at row centres
  const ARROW_X = TITLE_COL + TICK_COL + 8;
  const GRID_X = ARROW_X + 20;
  const BELOW = 78; // x arrow + ticks + x title
  const axesIn = interpolate(localMs, [100, 700], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  // Full matrix: cells pop in one by one. Highlight: all in fast, then spotlight.
  const cellIn = (i: number) =>
    hl === null
      ? reveal(localMs, i, 4, durationMs)
      : interpolate(localMs, [200, 600], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const spot = hl === null ? 0 : interpolate(localMs, [700, 1200], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const tick: React.CSSProperties = {
    fontSize: 17, fontWeight: 800, letterSpacing: 3, textTransform: "uppercase", color: MUTED, whiteSpace: "nowrap",
  };
  const axisTitle: React.CSSProperties = {
    fontSize: 22, fontWeight: 900, color: TEXT, letterSpacing: 2, textTransform: "uppercase", lineHeight: 1.1,
    textAlign: "center",
  };
  const rowCentre = (y: number) => (y ? CELL_H / 2 : CELL_H + GAP + CELL_H / 2);
  const colCentre = (x: number) => GRID_X + (x ? CELL_W + GAP + CELL_W / 2 : CELL_W / 2);
  return (
    <div style={{ position: "relative", width: GRID_X + GRID_W + 24, height: GRID_H + BELOW, margin: "0 auto" }}>
      {/* y axis title (rotated, centred on the grid) */}
      <div style={{ position: "absolute", left: TITLE_COL / 2 - GRID_H / 2, top: GRID_H / 2 - TITLE_COL / 2,
        width: GRID_H, height: TITLE_COL, transform: "rotate(-90deg)", display: "flex", alignItems: "center",
        justifyContent: "center", opacity: axesIn }}>
        <span style={axisTitle}>{y_axis.label}</span>
      </div>
      {/* y ticks at row centres */}
      {[1, 0].map((y) => (
        <div key={`yt${y}`} style={{ position: "absolute", left: TITLE_COL + TICK_COL / 2 - 60, top: rowCentre(y) - 12,
          width: 120, height: 24, transform: "rotate(-90deg)", display: "flex", justifyContent: "center",
          alignItems: "center", opacity: axesIn }}>
          <span style={tick}>{y ? y_axis.high || "High" : y_axis.low || "Low"}</span>
        </div>
      ))}
      {/* y arrow */}
      <div style={{ position: "absolute", left: ARROW_X, top: GRID_H * (1 - axesIn), width: 5, height: GRID_H * axesIn,
        background: speakerColor, borderRadius: 3, boxShadow: `0 0 14px ${speakerColor}` }} />
      <div style={{ position: "absolute", left: ARROW_X - 8, top: -14, width: 0, height: 0, opacity: axesIn,
        borderLeft: "10px solid transparent", borderRight: "10px solid transparent", borderBottom: `16px solid ${speakerColor}` }} />
      {/* x arrow */}
      <div style={{ position: "absolute", left: ARROW_X, top: GRID_H + 10, height: 5, width: (GRID_X - ARROW_X + GRID_W) * axesIn,
        background: speakerColor, borderRadius: 3, boxShadow: `0 0 14px ${speakerColor}` }} />
      <div style={{ position: "absolute", left: GRID_X + GRID_W - 2, top: GRID_H + 2, width: 0, height: 0, opacity: axesIn,
        borderTop: "10px solid transparent", borderBottom: "10px solid transparent", borderLeft: `16px solid ${speakerColor}` }} />
      {/* x ticks at column centres + x title */}
      {[0, 1].map((x) => (
        <div key={`xt${x}`} style={{ position: "absolute", left: colCentre(x) - 150, top: GRID_H + 20, width: 300,
          textAlign: "center", opacity: axesIn }}>
          <span style={tick}>{x ? x_axis.high || "High" : x_axis.low || "Low"}</span>
        </div>
      ))}
      <div style={{ position: "absolute", left: GRID_X, top: GRID_H + 44, width: GRID_W, opacity: axesIn, ...axisTitle }}>
        {x_axis.label} →
      </div>
      {/* cells */}
      {cells.map((c, i) => {
        const r = cellIn(i);
        const isHl = hl === i;
        const dim = hl === null ? 1 : isHl ? 1 : 1 - 0.55 * spot;
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: GRID_X + (c.x ? CELL_W + GAP : 0),
              top: c.y ? 0 : CELL_H + GAP,
              width: CELL_W,
              height: CELL_H,
              boxSizing: "border-box",
              padding: "14px 26px",
              borderRadius: 18,
              background: isHl ? `linear-gradient(160deg, ${speakerColor}55 0%, rgba(14,14,34,0.94) 75%)` : PANEL,
              border: `${isHl ? 4 : 2}px solid ${isHl ? speakerColor : speakerColor + "77"}`,
              boxShadow: isHl ? `0 0 ${22 + 24 * spot}px ${speakerColor}aa, 0 16px 36px rgba(0,0,0,0.5)` : "0 12px 30px rgba(0,0,0,0.45)",
              opacity: r * dim,
              transform: `scale(${(0.9 + 0.1 * r) * (isHl ? 1 + 0.03 * spot : 1)})`,
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              gap: 6,
              textAlign: "center",
            }}
          >
            <div style={{ fontSize: 34, fontWeight: 900, lineHeight: 1.1, color: TEXT }}>{c.label}</div>
            {c.detail ? <div style={{ fontSize: 22, color: MUTED, lineHeight: 1.25 }}>{c.detail}</div> : null}
          </div>
        );
      })}
    </div>
  );
};

const TextCard: React.FC<DiagramOverlayProps & { quote?: boolean }> = ({ nodes = [], title, localMs, speakerColor, quote }) => {
  const r = interpolate(localMs, [0, 500], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const nd = nodes[0] || { label: "", detail: "" };
  return (
    <div
      style={{
        width: 1300,
        margin: "0 auto",
        padding: "46px 60px",
        borderRadius: 26,
        background: PANEL,
        borderLeft: `10px solid ${speakerColor}`,
        opacity: r,
        transform: `translateY(${(1 - r) * 20}px)`,
      }}
    >
      {quote ? (
        <>
          <div style={{ fontFamily: "Georgia, serif", fontSize: 50, lineHeight: 1.3, color: TEXT, fontStyle: "italic" }}>
            “{nd.label}”
          </div>
          {title ? <div style={{ fontSize: 28, color: speakerColor, marginTop: 22, fontWeight: 700 }}>— {title}</div> : null}
        </>
      ) : (
        <>
          <div style={{ fontSize: 22, letterSpacing: 5, textTransform: "uppercase", color: speakerColor, fontWeight: 800 }}>{nd.label}</div>
          <div style={{ fontFamily: "Georgia, serif", fontSize: 64, fontWeight: 700, color: TEXT, margin: "12px 0 18px" }}>{title}</div>
          <div style={{ fontSize: 32, color: MUTED, lineHeight: 1.35 }}>{nd.detail}</div>
        </>
      )}
    </div>
  );
};

export const DiagramOverlay: React.FC<DiagramOverlayProps> = (props) => {
  const { kind, title, localMs, speakerColor } = props;
  const showTitle = kind !== "definition" && kind !== "quote";
  let body: React.ReactNode;
  switch (kind) {
    case "flow":
      body = <Flow {...props} />;
      break;
    case "hub":
      body = <Hub {...props} />;
      break;
    case "comparison":
      body = <Comparison {...props} />;
      break;
    case "timeline":
      body = <Timeline {...props} />;
      break;
    case "definition":
      body = <TextCard {...props} />;
      break;
    case "quote":
      body = <TextCard {...props} quote />;
      break;
    case "quadrant":
      body = <Quadrant {...props} />;
      break;
    default:
      body = <Pillars {...props} />;
  }
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        // quadrant sits high so its bottom row clears multi-line captions
        justifyContent: kind === "quadrant" ? "flex-start" : "center",
        paddingTop: kind === "quadrant" ? 14 : 0,
        boxSizing: "border-box",
        alignItems: "center",
        fontFamily: FONT,
        color: TEXT,
      }}
    >
      {showTitle ? <Title text={title} color={speakerColor} localMs={localMs} /> : null}
      {body}
    </div>
  );
};
