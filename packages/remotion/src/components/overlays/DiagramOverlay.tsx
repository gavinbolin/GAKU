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
 */

export interface DiagramNode {
  label: string;
  detail?: string;
}

export interface DiagramColumn {
  label: string;
  points: string[];
}

export interface DiagramOverlayProps {
  kind: string;
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
        justifyContent: "center",
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
