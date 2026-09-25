# TTS pronunciation lexicon

`config/tts_lexicon.json` fixes names the TTS engines mispronounce (e.g.
**Politzer**, which Kokoro read so that whisper heard "Pulitzer").

**Where it's applied:** only to the text sent to TTS:
`packages/mcp-server/src/tools/generate_audio.ts` rewrites a *copy* of the
episode before handing it to the Kokoro / VibeVoice backend. `episode.json`,
karaoke captions, bullets, titles and YouTube metadata keep the written
spelling.

**Karaoke timing:** captions are script-anchored (`visual_queue._script_anchored_timings`).
Whisper may transcribe the respelling ("Pahlitzer", or variants listed in `heard_as` such as "Palitzer") -- `tts_lexicon.collapse_spoken_tokens`
folds those tokens back to the written word before alignment, so the displayed
word keeps the correct spelling *and* the timing of the spoken audio.

## Adding an entry

```json
{ "written": "Politzer", "spoken": "Pahlitzer", "backends": ["kokoro", "vibevoice"],
  "heard_as": ["Palitzer", "Pallitzer"], "notes": "why" }
```

* `written` -- exact word as it appears in the script (matched whole-word, case-insensitive;
  possessives like `Politzer's` are handled, capitalization is preserved).
* `spoken` -- plain-English respelling the TTS reads correctly. Prefer ordinary spellings
  ("Pahlitzer", "Nee-ahm") over IPA; Kokoro and VibeVoice both read plain text.
* `backends` -- limit to `kokoro` / `vibevoice` (empty = all).
* `heard_as` (optional) -- how whisper spells the respelled audio; folded back to the
  written word for karaoke timing. Only list spellings that are not other real words
  used in the script.

Avoid Kokoro's inline IPA syntax (`[word](/ipa/)`): in the 2026-09-25 A/B it was
heard as "Pulitzer" 4/4, while "Pahlitzer" was never heard as Pulitzer.

Check a new respelling with:

```powershell
& $py podcast-pipeline\tests\test_pipeline_upgrade.py   # substitution + caption preservation
```

and, with Kokoro running, synthesize a one-line clip and listen / whisper it.
