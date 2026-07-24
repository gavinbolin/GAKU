import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface TimelineVisualProps {
  events: Array<{ year: string; label: string; description?: string }>;
  title?: string;
}

const BG = "#1a1a2e";
const TEXT = "#f5f0e8";
const ACCENT = "#7C3AED";
const MUTED = "rgba(245, 240, 232, 0.55)";

export const TimelineVisual: React.FC<TimelineVisualProps> = ({
  events,
  title,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const titleOpacity = interpolate(frame, [0, 14], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const lineStart = 10;
  const lineEnd = Math.floor(durationInFrames * 0.4);
  const lineProgress = interpolate(frame, [lineStart, lineEnd], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const safeEvents = events.slice(0, 6);
  const eventStartBase = 18;
  const perEventFrames = 18;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: BG,
        color: TEXT,
        fontFamily: "Georgia, 'Times New Roman', serif",
        padding: "80px 120px",
        justifyContent: "center",
      }}
    >
      {title ? (
        <div
          style={{
            fontSize: 48,
            fontWeight: 700,
            textAlign: "center",
            opacity: titleOpacity,
            marginBottom: 100,
          }}
        >
          {title}
        </div>
      ) : null}

      <div
        style={{
          position: "relative",
          width: "100%",
          height: 400,
        }}
      >
        <div
          style={{
            position: "absolute",
            top: "50%",
            left: 0,
            height: 4,
            width: `${lineProgress * 100}%`,
            backgroundColor: ACCENT,
            transform: "translateY(-50%)",
            borderRadius: 2,
          }}
        />

        {safeEvents.map((ev, i) => {
          const xPct =
            safeEvents.length === 1
              ? 50
              : (i / (safeEvents.length - 1)) * 100;
          const start = eventStartBase + i * perEventFrames;
          const sp = spring({
            frame: frame - start,
            fps,
            config: { damping: 16, stiffness: 130, mass: 0.6 },
          });
          const opacity = interpolate(sp, [0, 1], [0, 1]);
          const scale = interpolate(sp, [0, 1], [0, 1]);
          const above = i % 2 === 0;

          return (
            <div
              key={i}
              style={{
                position: "absolute",
                left: `${xPct}%`,
                top: 0,
                bottom: 0,
                transform: "translateX(-50%)",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                opacity,
              }}
            >
              {above ? (
                <div
                  style={{
                    transform: `scale(${scale})`,
                    textAlign: "center",
                    marginBottom: 32,
                    maxWidth: 240,
                  }}
                >
                  <div
                    style={{
                      fontSize: 26,
                      fontWeight: 700,
                      lineHeight: 1.2,
                    }}
                  >
                    {ev.label}
                  </div>
                  {ev.description ? (
                    <div
                      style={{
                        fontSize: 16,
                        color: MUTED,
                        marginTop: 6,
                        lineHeight: 1.3,
                      }}
                    >
                      {ev.description}
                    </div>
                  ) : null}
                </div>
              ) : (
                <div style={{ flex: 1 }} />
              )}

              <div
                style={{
                  width: 4,
                  height: 56,
                  backgroundColor: ACCENT,
                  transform: `scaleY(${scale})`,
                  transformOrigin: "center",
                }}
              />
              <div
                style={{
                  width: 22,
                  height: 22,
                  borderRadius: "50%",
                  backgroundColor: ACCENT,
                  border: `4px solid ${BG}`,
                  marginTop: -11,
                  transform: `scale(${scale})`,
                }}
              />

              {!above ? (
                <div
                  style={{
                    transform: `scale(${scale})`,
                    textAlign: "center",
                    marginTop: 32,
                    maxWidth: 240,
                  }}
                >
                  <div
                    style={{
                      fontSize: 26,
                      fontWeight: 700,
                      lineHeight: 1.2,
                    }}
                  >
                    {ev.label}
                  </div>
                  {ev.description ? (
                    <div
                      style={{
                        fontSize: 16,
                        color: MUTED,
                        marginTop: 6,
                        lineHeight: 1.3,
                      }}
                    >
                      {ev.description}
                    </div>
                  ) : null}
                </div>
              ) : (
                <div style={{ flex: 1 }} />
              )}

              <div
                style={{
                  position: "absolute",
                  top: above ? "calc(50% + 60px)" : "calc(50% - 90px)",
                  fontFamily: "system-ui, -apple-system, sans-serif",
                  fontSize: 22,
                  letterSpacing: 3,
                  color: ACCENT,
                  fontWeight: 700,
                  transform: `scale(${scale})`,
                }}
              >
                {ev.year}
              </div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
