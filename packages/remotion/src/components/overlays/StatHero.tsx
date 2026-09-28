import React from "react";
import { interpolate } from "remotion";
import { countUpAt, isSimpleSingleNumber, isStaticStat, LEADING_QUALIFIER_RE } from "./StatOverlay";

/**
 * StatHero -- one dedicated, full-stage hit per key stat (2026-09-25 items 1+7).
 *
 * Value rules (unchanged from StatOverlay, Gavin 2026-09-24):
 *   - Ranges / multi-number ("25-35% faster", "5-10%") render the FULL correct
 *     string statically -- only opacity/scale animate.
 *   - Simple single numbers ("85%", "10,000 versions") count up as whole
 *     values with proper formatting (grouping + decimals preserved).
 *     Never digit-roll, never show a wrong intermediate string.
 * Companions (e.g. "180 countries", "130 languages") follow the same rules.
 *
 * 2026-09-28 (items 1 + 6):
 *   - `landMs` = ms (relative to this hero's start) when the hosts SAY the
 *     number. Any count-up finishes by then; if there isn't room (< 450 ms)
 *     the value simply snaps in. The viewer never sees 82% while hearing 90%.
 *   - Limits / approximations ("Up to 6.9%", "Over $4 billion", "~40%") are
 *     static: the qualifier renders as a kicker, the number never rolls.
 */

export interface StatCompanion {
  value: string;
  label: string;
}

export interface StatHeroProps {
  label: string;
  value: string;
  subtext?: string;
  companions?: StatCompanion[];
  localMs: number;
  durationMs: number;
  speakerColor: string;
  /** ms after the hero appears when the number is spoken (alignment word time). */
  landMs?: number;
  /** Planner verdict; false forces a static snap. */
  countUp?: boolean;
}

const TEXT = "#f5f0e8";
const COUNT_MS = 1300;

// Big numeric core + trailing words ("25-35%" + "faster").
const CORE_RE =
  /^(\S*?\d[\d,.]*(?:\s*[-–—]\s*\d[\d,.]*)?\s*(?:%|x|×|k|K|M|B|bn|T)?)(?:\s+(.*))?$/;

export const splitStatValue = (raw: string): { core: string; rest: string } => {
  const v = (raw || "").trim();
  const m = CORE_RE.exec(v);
  if (!m) return { core: v, rest: "" };
  return { core: m[1].trim(), rest: (m[2] || "").trim() };
};

const RANGE_ANY_RE = /\d\s*(?:[-–—]|to)\s*\d/i;

const MIN_COUNT_MS = 450;
const LAND_MARGIN_MS = 120;

/** Count window that ends at/before the spoken number; 0 => static snap. */
export const countWindowMs = (landMs?: number, countUp?: boolean): number => {
  if (countUp === false) return 0;
  const w = landMs === undefined ? COUNT_MS : Math.min(COUNT_MS, landMs - LAND_MARGIN_MS);
  return w >= MIN_COUNT_MS ? w : 0;
};

const display = (core: string, localMs: number, delayMs = 0, countable = true, windowMs = COUNT_MS): string => {
  if (!countable || windowMs <= 0 || !isSimpleSingleNumber(core)) return core; // static, full string
  const t = (localMs - delayMs) / windowMs;
  return countUpAt(core, t);
};

export const StatHero: React.FC<StatHeroProps> = ({
  label,
  value,
  subtext,
  companions,
  localMs,
  durationMs,
  speakerColor,
  landMs,
  countUp,
}) => {
  const qm = LEADING_QUALIFIER_RE.exec(value || "");
  const qualifier = qm ? qm[1] : "";
  const body = qm ? (value || "").slice(qm[0].length) : value;
  const { core, rest } = splitStatValue(body);
  // Any range / limit / approximation / extra digits => whole value static.
  const countable = !isStaticStat(value) && !RANGE_ANY_RE.test(value) && !/\d/.test(rest);
  const windowMs = countWindowMs(landMs, countUp);
  const inT = interpolate(localMs, [0, 450], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const scale = interpolate(localMs, [0, 380, 560], [0.86, 1.03, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const subOpacity = interpolate(localMs, [500, 900], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const comps = (companions || []).slice(0, 3);
  const coreSize = core.length > 7 ? 168 : 200;

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
        color: TEXT,
        textAlign: "center",
        opacity: inT,
        transform: `scale(${scale})`,
      }}
    >
      {label ? (
        <div
          style={{
            fontSize: 30,
            fontWeight: 700,
            letterSpacing: 6,
            textTransform: "uppercase",
            color: "rgba(245,240,232,0.82)",
            marginBottom: 6,
            maxWidth: 1400,
          }}
        >
          {label}
        </div>
      ) : null}
      <div style={{ display: "flex", alignItems: "baseline", gap: 26, justifyContent: "center" }}>
        {qualifier ? (
          <div
            style={{
              fontSize: 64,
              fontWeight: 800,
              color: TEXT,
              textTransform: "uppercase",
              letterSpacing: 2,
              textShadow: "0 4px 18px rgba(0,0,0,0.7)",
            }}
          >
            {qualifier}
          </div>
        ) : null}
        <div
          style={{
            fontSize: coreSize,
            fontWeight: 900,
            lineHeight: 1,
            letterSpacing: -4,
            color: speakerColor,
            textShadow: `0 0 60px ${speakerColor}66, 0 8px 28px rgba(0,0,0,0.65)`,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {display(core, localMs, 0, countable, windowMs)}
        </div>
        {rest ? (
          <div style={{ fontSize: 64, fontWeight: 800, color: TEXT, textShadow: "0 4px 18px rgba(0,0,0,0.7)" }}>
            {rest}
          </div>
        ) : null}
      </div>
      {comps.length ? (
        <div style={{ display: "flex", gap: 90, marginTop: 34, opacity: subOpacity }}>
          {comps.map((c, i) => (
            <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
              <div
                style={{
                  fontSize: 96,
                  fontWeight: 900,
                  lineHeight: 1,
                  color: TEXT,
                  textShadow: `0 0 30px ${speakerColor}55, 0 6px 18px rgba(0,0,0,0.7)`,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {display(c.value, localMs, 500 + i * 250, !RANGE_ANY_RE.test(c.value) && !isStaticStat(c.value))}
              </div>
              <div
                style={{
                  fontSize: 26,
                  fontWeight: 700,
                  letterSpacing: 4,
                  textTransform: "uppercase",
                  color: speakerColor,
                  marginTop: 8,
                }}
              >
                {c.label}
              </div>
            </div>
          ))}
        </div>
      ) : null}
      {subtext && !comps.length ? (
        <div
          style={{
            fontSize: 34,
            fontWeight: 500,
            color: "rgba(245,240,232,0.86)",
            marginTop: 26,
            maxWidth: 1300,
            opacity: subOpacity,
            textShadow: "0 3px 14px rgba(0,0,0,0.7)",
          }}
        >
          {subtext}
        </div>
      ) : null}
    </div>
  );
};
