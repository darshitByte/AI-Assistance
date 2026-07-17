# Speech-to-Text in the Chat Composer — Design

**Date:** 2026-07-17
**Scope:** Frontend only (`frontend/src/App.jsx` + `frontend/src/index.css`). No backend, no new dependency.

## Goal

Let the user speak into the chat instead of typing. A mic button in the composer
starts speech recognition; recognized words fill the message input. The user
still presses **Send** (or Enter) to submit — recognition only produces text.

## Approach

Use the browser-native **Web Speech API** (`SpeechRecognition` /
`webkitSpeechRecognition`). Client-side, zero dependencies, no API key, no
backend round-trip. Works in Chrome/Edge on localhost or HTTPS. Firefox/Safari
lack support — handled by degradation below.

Rejected: backend STT (Whisper). Cross-browser but adds an endpoint, audio
upload, latency, and a dependency — overkill for a POC.

## Components

### `useSpeech` hook (in `App.jsx`)
Encapsulates the Web Speech API so the composer stays declarative.

- **Returns:** `{ supported, listening, toggle }`.
- **`supported`** — `false` when neither `SpeechRecognition` nor
  `webkitSpeechRecognition` exists. Gates whether the mic button renders.
- **`listening`** — drives the button's active/pulsing state.
- **`toggle`** — starts recognition if idle, stops it if active.
- Takes a callback (e.g. `onText`) to push transcript into the composer.
- Config: `lang = 'en-US'`, `interimResults = true`, `continuous = false`
  (auto-stops on silence).
- Cleans up the recognition instance on unmount / stop.

### Mic button (in the existing composer `<form>`)
- Rendered left of the `<input>`, only when `supported`.
- `type="button"` (must NOT submit the form).
- `aria-label` toggles between "Start voice input" / "Stop voice input".
- Class toggles a listening modifier for the pulse animation.
- `disabled` while `pending` (checkout buttons up), matching the input.

## Data flow

1. Click mic → `toggle()` → `recognition.start()` → `listening = true`.
2. As the user speaks, `onresult` fires with interim + final transcripts.
3. Hook calls `onText(transcript)`; composer sets the input value. Transcript is
   **appended** to any already-typed text (so typing + speaking mix), with the
   current utterance's interim text replacing the previous interim tail.
4. Silence (or a second mic click) → `onend` → `listening = false`.
5. User presses Send / Enter — the normal `send(input)` path. Unchanged.

## Error handling / degradation

- **Unsupported browser:** `supported === false` → button not rendered. No broken UI.
- **Permission denied / `onerror`:** stop, reset `listening`, show a one-time
  inline hint near the composer (e.g. "Microphone unavailable — check
  permissions"). Non-blocking; typing still works.
- **HTTP (non-localhost):** the API rejects; treated as the error case above.

## Testing

No unit test — this is a browser-API-driven UI toggle with no business logic.
Verify by driving the running app with Playwright:
- Mic button renders (in Chromium).
- Clicking it toggles the listening class/state.
(Actual audio recognition can't be exercised headless; that's a manual check.)

## Out of scope

- Text-to-speech (reading assistant replies aloud).
- Auto-submit after speaking — submit stays manual, by request.
- Non-English languages / language picker.
- Backend transcription / cross-browser support.
