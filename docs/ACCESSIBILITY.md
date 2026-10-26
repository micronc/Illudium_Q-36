# Accessibility for blind and low-vision threat researchers

Illudium-Q36 is usable with a screen reader and keyboard. This note covers what to set up and what the console provides.

## What you need on your machine

1. **A screen reader** (required for full non-visual use)
   - Windows: [NVDA](https://www.nvaccess.org/) (free) or JAWS
   - macOS: VoiceOver (`Cmd + F5`)
   - Windows built-in: Narrator (`Win + Ctrl + Enter`)
2. **A modern browser** — Chrome, Edge, or Firefox
3. **Optional:** enable **508 Mode** in the top bar for high contrast and larger text (helps low vision; screen readers work with or without it)

## Built-in console features

| Feature | How |
|--------|-----|
| Skip to chat | Tab to “Skip to analyst chat”, Enter |
| Speak replies | Checkbox under the composer (browser text-to-speech) |
| Voice input | Mic button, or focus it and activate |
| Read last answer aloud | `Alt + R` |
| Read ATLAS panel aloud | `Alt + A` |
| Read intel file list aloud | `Alt + I` |
| Stop speech | `Alt + S` or `Escape` |
| Jump to intel corpus | `Alt + 1` |
| Jump to chat / messages | `Alt + 2` |
| Jump to message box | `Alt + 3` |
| Keyboard help | `Alt + /` or `?` (when not typing in a field) |
| Live updates | New chat lines and status text are announced via ARIA live regions |

## Recommended workflow

1. Start your screen reader, then open `http://127.0.0.1:5000`.
2. Press `Alt + /` once to hear the shortcut list (or open Keyboard help).
3. Add intel (`Alt + 1`), then chat or generate a briefing (`Alt + 2` / `Alt + 3`).
4. After a briefing, press `Alt + R` to hear the full report, then `Alt + A` for ATT&CK ↔ ATLAS separation/overlap.

## Notes and limits

- Screen readers remain the primary way to explore headings, buttons, and links; Illudium shortcuts add **section read-aloud** on top of that.
- Browser TTS quality varies by OS voice pack.
- Voice *recognition* (mic):
  - **Chrome / Edge** — built-in Web Speech API (click mic, speak, auto-sends).
  - **Firefox** — no usable Web Speech Recognition; Illudium records audio and transcribes with **Whisper** when `OPENAI_API_KEY` or `GROQ_API_KEY` is set in `.env` (click mic to record, click again to stop). `DEMO_MODE=true` returns a sample transcript for UI testing.
  - Microphone permission is required in all browsers.
- Speak replies (TTS) uses `speechSynthesis` and works in Firefox, Chrome, and Edge.
- This is not a substitute for a full formal VPAT against every WCAG criterion; it is practical support for blind/low-vision researchers using the analyst console.
