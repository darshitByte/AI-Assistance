# Speech-to-Text Chat Composer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a mic button to the chat composer that transcribes the user's speech into the message input using the browser-native Web Speech API; the user still presses Send.

**Architecture:** Frontend only. A `useSpeech` hook wraps `SpeechRecognition`/`webkitSpeechRecognition` and exposes `{ supported, listening, error, toggle }`. The existing composer `<form>` in `App.jsx` renders a mic button (only when `supported`) that calls `toggle`; recognized text is appended to the `input` state via `setInput`. CSS adds the button style + a pulse animation for the listening state.

**Tech Stack:** React (Vite), Web Speech API (browser-native, no dependency), plain CSS.

**Reference:** spec at `docs/superpowers/specs/2026-07-17-speech-to-text-chat-design.md`.

**Testing note:** This feature is a browser-API-driven UI toggle with no business logic — per the spec there is no unit test. Verification is manual + a Playwright smoke check (button renders, toggles state). Steps below reflect that: build, then verify in the running app rather than red/green TDD.

---

## Task 1: Add the `useSpeech` hook

**Files:**
- Modify: `frontend/src/App.jsx` (add hook near the top, after the imports at line 1-5, before the `App` component)

- [ ] **Step 1: Add the hook**

Insert this hook after the imports (around line 6 of `frontend/src/App.jsx`), above the `App` component definition:

```jsx
// Speech-to-text via the browser-native Web Speech API (Chrome/Edge; localhost or HTTPS).
// ponytail: native platform feature, no dependency. onText receives the live transcript.
function useSpeech(onText) {
  const Recognition =
    typeof window !== "undefined" &&
    (window.SpeechRecognition || window.webkitSpeechRecognition);
  const supported = !!Recognition;

  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  const recRef = useRef(null);
  const onTextRef = useRef(onText);
  onTextRef.current = onText; // keep latest callback without re-creating recognition

  const toggle = useCallback(() => {
    if (!supported) return;
    if (recRef.current) {
      recRef.current.stop(); // fires onend → resets listening
      return;
    }
    const rec = new Recognition();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = false; // auto-stops on silence
    rec.onresult = (e) => {
      let transcript = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        transcript += e.results[i][0].transcript;
      }
      onTextRef.current(transcript);
    };
    rec.onerror = (e) => {
      setError(
        e.error === "not-allowed" || e.error === "service-not-allowed"
          ? "Microphone unavailable — check browser permissions."
          : ""
      );
    };
    rec.onend = () => {
      recRef.current = null;
      setListening(false);
    };
    recRef.current = rec;
    setError("");
    setListening(true);
    rec.start();
  }, [supported, Recognition]);

  // Stop recognition if the component unmounts mid-listen.
  useEffect(() => () => recRef.current?.stop(), []);

  return { supported, listening, error, toggle };
}
```

- [ ] **Step 2: Add `useCallback` to the React import**

Change line 1 of `frontend/src/App.jsx` from:

```jsx
import { useEffect, useRef, useState } from "react";
```

to:

```jsx
import { useCallback, useEffect, useRef, useState } from "react";
```

- [ ] **Step 3: Verify the app still builds**

Run: `cd frontend && npm run build`
Expected: build succeeds with no errors (the hook is defined but not yet used — that's fine, it's a top-level function).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "feat(chat): add useSpeech hook wrapping Web Speech API"
```

---

## Task 2: Wire the hook + mic button into the composer

**Files:**
- Modify: `frontend/src/App.jsx` — call the hook inside `App` (near the other state, around line 48-54), and add the mic button in the composer `<form>` (lines 518-536)

- [ ] **Step 1: Call the hook inside the `App` component**

Directly after the composer-related state (after line 54, `const [pending, setPending] = useState(null);`), add:

```jsx
  // Voice input: append recognized speech to whatever's in the composer.
  // The utterance's live/interim text replaces itself; committed text stays.
  const speechBaseRef = useRef("");
  const speech = useSpeech((transcript) => {
    setInput((speechBaseRef.current + transcript).trimStart());
  });
```

- [ ] **Step 2: Capture the pre-speech input when starting to listen**

The transcript from a single utterance is cumulative, so it must be appended to
whatever text existed *before* listening began — not re-appended on every event.
Wrap the button's click to snapshot `input` when starting. Replace the composer
`<form>` block (lines 518-536) with:

```jsx
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        {speech.supported && (
          <button
            type="button"
            className={`composer__mic${speech.listening ? " composer__mic--on" : ""}`}
            onClick={() => {
              if (!speech.listening) {
                // Snapshot current text so the transcript appends after it (with a space).
                speechBaseRef.current = input ? input.replace(/\s*$/, "") + " " : "";
              }
              speech.toggle();
            }}
            disabled={!!pending}
            aria-label={speech.listening ? "Stop voice input" : "Start voice input"}
            title={speech.error || (speech.listening ? "Stop voice input" : "Start voice input")}
          >
            {speech.listening ? "⏺" : "🎤"}
          </button>
        )}
        <input
          className="composer__input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={pending ? "Use the buttons above to continue…" : "Ask for a product…"}
          disabled={!!pending}
          autoFocus
        />
        <button className="composer__send" type="submit" disabled={loading || !!pending || !input.trim()}>
          {loading ? "…" : "Send"}
        </button>
      </form>
```

- [ ] **Step 3: Verify the app builds**

Run: `cd frontend && npm run build`
Expected: build succeeds with no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "feat(chat): add mic button to composer, append speech to input"
```

---

## Task 3: Style the mic button + listening pulse

**Files:**
- Modify: `frontend/src/index.css` — add after the `.composer__send:disabled` block (after line 554)

- [ ] **Step 1: Add the CSS**

Append after line 554 of `frontend/src/index.css`:

```css
.composer__mic {
  flex: none;
  width: 46px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--paper);
  color: var(--ink);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease, transform 0.12s ease;
}

.composer__mic:hover:not(:disabled) {
  border-color: var(--leaf-bright);
  transform: translateY(-1px);
}

.composer__mic:disabled {
  color: var(--ink-soft);
  cursor: default;
}

.composer__mic--on {
  background: #e5484d;
  border-color: #e5484d;
  color: #fff;
  animation: mic-pulse 1.2s ease-in-out infinite;
}

@keyframes mic-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(229, 72, 77, 0.5); }
  50% { box-shadow: 0 0 0 6px rgba(229, 72, 77, 0); }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/index.css
git commit -m "style(chat): mic button + listening pulse animation"
```

---

## Task 4: Verify in the running app

**Files:** none (verification only)

- [ ] **Step 1: Rebuild the frontend container**

Run: `docker compose up -d --build --force-recreate --no-deps frontend`
Expected: frontend container rebuilds and starts.

- [ ] **Step 2: Playwright smoke check (Chromium)**

Using the Playwright browser tools:
1. Navigate to `http://localhost:3000` and log in (admin / admin@123).
2. Snapshot the page — confirm a mic button (`🎤`, `aria-label="Start voice input"`) appears left of the message input.
3. Click the mic button. Note: the browser will prompt for microphone permission; grant or dismiss it. Confirm the button either enters the listening state (`⏺`, `composer__mic--on`) or, if permission is denied, resets and its `title` shows the permission hint.

Expected: button renders and its click toggles state / degrades cleanly. (Actual speech recognition requires a real mic and can't be exercised headless — that is a manual check.)

- [ ] **Step 3: Manual voice check (developer, real mic)**

In Chrome/Edge on `http://localhost:3000`: type nothing, click mic, say "show me apples", confirm the words fill the input, then press Send and confirm the normal chat flow runs. Repeat with pre-typed text to confirm the transcript appends after it.

- [ ] **Step 4: Final commit (if any doc/tweaks needed)**

```bash
git add -A
git commit -m "chore(chat): finalize speech-to-text composer" --allow-empty
```

---

## Self-review notes

- **Spec coverage:** hook w/ `supported`/`listening`/`error`/`toggle` (Task 1) ✓; mic button in composer, only when supported, `type="button"`, disabled on `pending`, aria-label toggle (Task 2) ✓; append-to-existing-text behavior (Task 2, `speechBaseRef`) ✓; interim results (`interimResults = true`) ✓; `en-US` ✓; auto-stop (`continuous = false`) ✓; unmount cleanup (Task 1 useEffect) ✓; unsupported → no render (Task 2 `speech.supported &&`) ✓; permission-denied hint (Task 1 `onerror` → `error`, surfaced via `title`) ✓; pulse animation (Task 3) ✓; Playwright + manual verification (Task 4) ✓.
- **No auto-submit, no TTS, no backend, no language picker** — all confirmed out of scope.
- **Type consistency:** `useSpeech` returns `{ supported, listening, error, toggle }` in Task 1 and is consumed with exactly those names in Task 2. Class names `composer__mic` / `composer__mic--on` match between Task 2 and Task 3.
