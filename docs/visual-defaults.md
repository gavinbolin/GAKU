# Visual defaults (locked 2026-09-28)

Gavin approved these after the M7 rewatch ("Match Your AI Strategy to Your
Organization's Reality"). They are **defaults with no flags**: every run gets
them. Change them only on purpose, and update this file and
`tests/test_pipeline_upgrade.py` when you do.

## Karaoke captions + host colours
- The current word takes the **speaker's colour** and grows with
  `transform: scale()` (`KARAOKE_SCALE_PEAK` in `SegmentCard.tsx`). Font weight
  stays at 800, so nothing reflows.
- **No white box** or pill behind the active word (`backgroundColor: transparent`).
- Host colours come from the single registry `PROJECT GAKU/voice_colors.json`
  through `visual_queue.voice_persona()` (for example George #F59E0B,
  Adam #10B981). `research_podcast` stamps `hosts[].color` from it.
- Guarded by `test_karaoke_defaults_locked`.

## HBR episodes: chapter chips + Key Takeaways
- Chapter chips (`type: "chapter"` beats) are planned mid-episode by
  `visual_timeline`.
- A bullets overlay titled "Key Takeaways" / "Takeaways" / "Key lessons"
  (2–6 items) **always** renders as the Key Takeaways pillars card, even when
  pillars were already used earlier in the episode. The script prompt asks the
  recap segment for one.

## Rich bullets + 2x2 quadrant
- Rich bullet cards stay the default for lists.
- When the content is a **2x2 framework** (two axes that produce four named
  strategies or types), the planner renders a `DiagramOverlay kind="quadrant"`:
  - axis labels and four labelled cells, with the full matrix when the
    framework is introduced;
  - a highlighted cell in the segments about a single quadrant (these replace
    the "position" bullets);
  - a filled-in version for a case that spans quadrants.
- Sources, in order:
  1. a script `quadrant` overlay;
  2. a heuristic that needs both axes named in the script (a 2-item
     "dimensions" bullets card or two quoted terms after "two dimensions")
     and four cells found in bullets or titles.
- The planner never invents a quadrant. Plain lists stay bullets.

## Stat heroes (items 1, 5, 6)
- Every stat card carries `land_ms`, the whisper word time when the number is
  spoken.
  - A count-up finishes at or before `land_ms`: `StatHero` counts over
    `min(1300, land-120)` ms, and snaps in when there's less than about 450 ms
    of room.
  - Planned holds are at least 4.5 s after landing (`STAT_MIN_HOLD_MS`).
- Values with a range or qualifier ("up to", about, around, nearly, over, more
  than, ~, trailing +, 5-10%) are **static**. The qualifier renders as a
  kicker and the number never rolls. The planner restores the spoken qualifier
  ("90%" → "Up to 90%").

## Named-case B-roll
- `broll_queries.NAMED_CASES` maps named companies and cases to their
  domain, for example:
  - Zillow → homes and real estate
  - Pfizer/BioNTech → lab and vaccine
- Those queries are used first for windows that mention the case (or the
  previous ~15 s of speech). The robot, text and board-writing filters still
  apply.
