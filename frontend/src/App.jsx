import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Auth from "./Auth";
import { API_BASE, CURRENCY } from "./config";

// Flatten markdown reply text to something that reads naturally aloud.
// ponytail: regex strip, not a markdown parser — the reply set is simple prose + bullets.
function speakable(md) {
  return (md || "")
    .replace(/```[\s\S]*?```/g, " ") // code fences
    .replace(/`([^`]+)`/g, "$1") // inline code
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ") // images
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1") // links → link text
    .replace(/^\s{0,3}#{1,6}\s+/gm, "") // headings
    .replace(/^\s{0,3}>\s?/gm, "") // blockquotes
    .replace(/^\s{0,3}[-*+]\s+/gm, "") // bullet markers
    .replace(/[*_~]/g, "") // emphasis marks
    .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2190}-\u{21FF}\u{2B00}-\u{2BFF}️]/gu, "") // emoji/pictographs
    .replace(/\s+/g, " ")
    .trim();
}

// Text-to-speech via the browser-native Web Speech API (all modern browsers).
// ponytail: native platform feature, no dependency. enabled persists in localStorage.
function useTTS() {
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;
  const [enabled, setEnabled] = useState(
    () => supported && localStorage.getItem("tts") !== "off"
  );

  const cancel = useCallback(() => {
    if (supported) window.speechSynthesis.cancel();
  }, [supported]);

  const speak = useCallback(
    (text) => {
      if (!supported) return;
      const say = speakable(text);
      if (!say) return;
      window.speechSynthesis.cancel(); // interrupt any in-flight speech
      const u = new SpeechSynthesisUtterance(say);
      u.lang = "en-US";
      window.speechSynthesis.speak(u);
    },
    [supported]
  );

  const toggle = useCallback(() => {
    setEnabled((on) => {
      const next = !on;
      localStorage.setItem("tts", next ? "on" : "off");
      if (!next) window.speechSynthesis.cancel(); // muting stops current speech
      return next;
    });
  }, []);

  useEffect(() => cancel, [cancel]); // stop speaking on unmount

  return { supported, enabled, toggle, speak, cancel };
}

// Speech-to-text via the browser-native Web Speech API (Chrome/Edge; localhost or HTTPS).
// ponytail: native platform feature, no dependency. onText receives the live transcript.
// start() begins a session; stop() ends it — the caller decides (via its own ✓/✕ UI)
// whether to keep the transcript. Chrome ends recognition on silence, so onend restarts
// it while `activeRef` is set, keeping the bar live until the user confirms/cancels.
function useSpeech(onText) {
  const Recognition =
    typeof window !== "undefined" &&
    (window.SpeechRecognition || window.webkitSpeechRecognition);
  const supported = !!Recognition;

  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  const recRef = useRef(null);
  const activeRef = useRef(false); // user still wants to record?
  const committedRef = useRef(""); // transcript from prior (auto-restarted) sessions
  const onTextRef = useRef(onText);
  onTextRef.current = onText; // keep latest callback without re-creating recognition

  const start = useCallback(() => {
    if (!supported || recRef.current) return;
    activeRef.current = true;
    committedRef.current = "";
    const rec = new Recognition();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = true;
    rec.onresult = (e) => {
      let transcript = "";
      for (let i = 0; i < e.results.length; i++) transcript += e.results[i][0].transcript;
      onTextRef.current(committedRef.current + transcript);
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed")
        setError("Microphone unavailable — check browser permissions.");
    };
    rec.onend = () => {
      // Preserve this session's text, then restart if the user is still recording
      // (Chrome closes on silence). Guard by identity: stop() may have already
      // cleared recRef (or started a new session) — a stale onend must not touch it.
      const finals = Array.from(rec.results || [])
        .filter((r) => r.isFinal)
        .map((r) => r[0].transcript)
        .join("");
      committedRef.current += finals;
      if (activeRef.current && recRef.current === rec) {
        try {
          rec.start();
          return;
        } catch {
          /* start races the end event; fall through to stopped */
        }
      }
      if (recRef.current === rec) {
        recRef.current = null;
        setListening(false);
      }
    };
    recRef.current = rec;
    setError("");
    setListening(true);
    rec.start();
  }, [supported, Recognition]);

  const stop = useCallback(() => {
    // Clear recRef NOW so the next start() is never blocked, even if onend is
    // slow or never fires again on an already-ended recognition.
    activeRef.current = false;
    const rec = recRef.current;
    recRef.current = null;
    setListening(false);
    try {
      rec?.stop();
    } catch {
      /* already ended */
    }
  }, []);

  // Stop recognition if the component unmounts mid-listen.
  useEffect(() => () => stop(), [stop]);

  return { supported, listening, error, start, stop };
}

// Live mic-level waveform. Web Audio AnalyserNode reads real amplitude; each frame
// pushes a new sample to the right and scrolls the rest left. Bars sit at a thin
// baseline ("dots") when quiet and grow with your voice. Writes bar heights straight
// to the DOM (no React state) to stay smooth. Mic denied → stays a flat baseline.
// Also drives auto-send: once the user has spoken, SILENCE_MS of quiet fires onSilence.
const WAVE_BARS = 56;
const SILENCE_MS = 3000; // auto-send after this much silence following speech
const VOICE_LEVEL = 0.08; // RMS level counted as "speaking"
function VoiceWave({ onSilence }) {
  const ref = useRef(null);
  const onSilenceRef = useRef(onSilence);
  onSilenceRef.current = onSilence;
  useEffect(() => {
    let raf, ctx, stream, stopped = false;
    const levels = new Array(WAVE_BARS).fill(0);
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        ctx = new (window.AudioContext || window.webkitAudioContext)();
        if (ctx.state === "suspended") await ctx.resume();
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        ctx.createMediaStreamSource(stream).connect(analyser);
        const data = new Uint8Array(analyser.fftSize);
        const spans = ref.current ? Array.from(ref.current.children) : [];
        let last = 0, spoke = false, lastVoice = 0;
        const tick = (t) => {
          if (stopped) return;
          raf = requestAnimationFrame(tick);
          if (t - last < 160) return; // ~6 fps: one new sample per bar-scroll step
          last = t;
          analyser.getByteTimeDomainData(data);
          let sum = 0;
          for (let i = 0; i < data.length; i++) {
            const v = (data[i] - 128) / 128;
            sum += v * v;
          }
          const level = Math.min(1, Math.sqrt(sum / data.length) * 3.5); // RMS + gain
          levels.push(level);
          levels.shift(); // newest at the right, scroll left
          for (let i = 0; i < spans.length; i++) {
            spans[i].style.height = 10 + levels[i] * 90 + "%";
            spans[i].style.opacity = 0.35 + levels[i] * 0.65;
          }
          if (level > VOICE_LEVEL) { spoke = true; lastVoice = t; }
          else if (spoke && t - lastVoice > SILENCE_MS) {
            stopped = true; // fire once; App tears this component down
            onSilenceRef.current?.();
          }
        };
        raf = requestAnimationFrame(tick);
      } catch {
        /* mic unavailable/denied — bars stay at the flat baseline, no auto-send */
      }
    })();
    return () => {
      stopped = true;
      cancelAnimationFrame(raf);
      stream?.getTracks().forEach((tr) => tr.stop());
      ctx?.close();
    };
  }, []);
  return (
    <div className="voicebar__wave" ref={ref} aria-label="Listening">
      {Array.from({ length: WAVE_BARS }).map((_, i) => (
        <span key={i} />
      ))}
    </div>
  );
}

const SUGGESTIONS = [
  { emoji: "🥛", text: "What milk do you have?" },
  { emoji: "🥥", text: "Show me the coconut" },
];

const money = (n) => `${CURRENCY} ${Number(n || 0).toLocaleString()}`;

// Spoken summary of a placed order (TTS reads this; not rendered on screen).
function orderSpeech(o) {
  const items = (o.items || []).map((it) => `${it.name} times ${it.qty}`).join(", ");
  return [
    "Order placed successfully.",
    items && `Items: ${items}.`,
    o.total != null && `Total paid ${money(o.total)}.`,
    o.ship_to && `Ship to ${o.ship_to}.`,
    o.delivery_by && `Delivery by ${o.delivery_by}.`,
  ]
    .filter(Boolean)
    .join(" ");
}
const EMPTY_CART = { items: [], items_qty: 0, grand_total: 0, currency: CURRENCY };

const addrIcon = (label = "") => {
  const l = label.toLowerCase();
  if (l.includes("home")) return "🏠";
  if (l.includes("office") || l.includes("work")) return "🏢";
  return "📍";
};
// One-line address for the echo/payment card: full street→postcode for guests,
// falling back to the saved-address label/city for logged-in customers.
const addrLine = (a) => [a.street, a.city, a.region, a.postcode].filter(Boolean).join(", ") || a.label || a.city || "";
const payIcon = (code = "") => {
  const c = code.toLowerCase();
  if (c.includes("cash") || c.includes("cod")) return "💵";
  if (c.includes("bank") || c.includes("transfer")) return "🏦";
  if (c.includes("check")) return "🧾";
  if (c.includes("paypal")) return "🅿️";
  return "💳";
};

const MAX_PW_ATTEMPTS = 3; // wrong-password tries at checkout before we fall back to guest

// Full chat transcript (cards + checkout + order card included) is snapshotted to
// localStorage per session so a page refresh restores exactly what was on screen.
const chatKey = (sid) => `chat:${sid}`;
function restoreSnapshot(sid, greeting) {
  try {
    const saved = JSON.parse(localStorage.getItem(chatKey(sid)) || "null");
    if (!Array.isArray(saved) || saved.length <= 1) return null; // nothing beyond the greeting
    return [greeting, ...saved.slice(1)]; // fresh greeting (live product count) + snapshotted turns
  } catch {
    return null;
  }
}

// Which composer-gating `pending` state a still-open checkout card corresponds to.
// Used to resume a mid-checkout refresh: the picker restores live (JWT-backed calls
// still work), and this re-gates the composer so the flow behaves as if uninterrupted.
const PENDING_BY_KIND = { emailForm: "email", passwordForm: "login", addressPicker: "address", addressForm: "address", addressConfirm: "address", paymentPicker: "payment" };
function pendingFromMessages(msgs) {
  const last = [...msgs].reverse().find((m) => m.role === "bot" && m.kind);
  return last && !last.done ? PENDING_BY_KIND[last.kind] || null : null;
}

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem("token") || "");
  const [guestId, setGuestId] = useState(() => localStorage.getItem("guest_id") || "");
  const [username, setUsername] = useState(() => localStorage.getItem("username") || "");
  // Token obtained at the checkout login while browsing as a guest. Held in a ref
  // (not `token` state) so becoming authed mid-chat doesn't retrigger the load
  // effect and wipe the in-progress checkout. authFetch prefers it over the guest id.
  // Persisted to localStorage so a refresh mid-checkout keeps the authed identity —
  // otherwise the app reverts to guest, whose cart was already emptied by the merge.
  const checkoutTokenRef = useRef(localStorage.getItem("checkout_token") || "");
  const [sessionId, setSessionId] = useState(() => {
    let s = localStorage.getItem("session_id");
    if (!s) {
      s = crypto.randomUUID();
      localStorage.setItem("session_id", s);
    }
    return s;
  });
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState({ online: null });
  const [productCount, setProductCount] = useState(null);
  const [cart, setCart] = useState(EMPTY_CART);
  const [cartOpen, setCartOpen] = useState(false);
  const [pending, setPending] = useState(null); // null | "email" | "login" | "address" | "payment": in-chat checkout step gating the composer
  const [checkoutEmail, setCheckoutEmail] = useState(""); // email captured at the email-first checkout step
  const pwAttemptsRef = useRef(0); // wrong-password tries at checkout; MAX_PW_ATTEMPTS → fall back to guest
  // Voice input: recording bar (waveform + ✕/✓) replaces the composer while active.
  // Live transcript appends after whatever was already typed; confirm keeps it in the
  // field (brief "loading" beat first), cancel restores the pre-recording text.
  const [voice, setVoice] = useState("off"); // off | rec | loading
  const speechBaseRef = useRef("");
  const speech = useSpeech((transcript) =>
    setInput((speechBaseRef.current + transcript).trimStart())
  );
  const inputRef = useRef(""); // latest input, read by the silence auto-send
  inputRef.current = input;
  const abortRef = useRef(null); // in-flight /chat request, aborted by the stop button
  const hydratedRef = useRef(null); // session whose messages are loaded; gates the snapshot save
  const startVoice = () => {
    speechBaseRef.current = input ? input.replace(/\s*$/, "") + " " : "";
    setVoice("rec");
    speech.start();
  };
  const cancelVoice = () => {
    speech.stop();
    setInput(speechBaseRef.current.trimEnd()); // discard the transcript
    setVoice("off");
  };
  const confirmVoice = () => {
    speech.stop();
    setVoice("loading"); // transcript is already in the field; brief transcribing beat
    setTimeout(() => {
      setVoice("off");
      document.querySelector(".composer__input")?.focus();
    }, 600);
  };
  // 4s of silence after the user has spoken → stop and send the transcript automatically.
  const silentSend = () => {
    speech.stop();
    setVoice("off");
    if (!pending) send(inputRef.current); // send() trims + guards empty
  };
  const tts = useTTS();
  // Auto-speak only newly-appended live bot replies — never restored history,
  // session switches, resets, or in-place edits (all resync the count silently).
  const spokenCountRef = useRef(0);
  useEffect(() => {
    const appendedOne = messages.length === spokenCountRef.current + 1;
    const last = messages[messages.length - 1];
    const say = last?.speakText || last?.text; // speakText: spoken-only (e.g. order card)
    if (appendedOne && tts.enabled && last?.role === "bot" && say) {
      tts.speak(say);
    }
    spokenCountRef.current = messages.length;
  }, [messages, tts]);
  const [sessions, setSessions] = useState([]);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const logRef = useRef(null);

  function refreshSessions() {
    if (!token) return; // the sessions sidebar is JWT-only; a guest has none
    authFetch("/sessions")
      .then((r) => r.json())
      .then((d) => setSessions(d.sessions || []))
      .catch(() => {});
  }

  function authFetch(path, options = {}) {
    const authed = token || checkoutTokenRef.current;
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (authed) headers.Authorization = `Bearer ${authed}`;
    else if (guestId) headers["X-Guest-Id"] = guestId;
    return fetch(`${API_BASE}${path}`, { ...options, headers }).then((r) => {
      // Only bounce to the login screen for a real logged-in session; a guest
      // hitting an authed-only route just gets the error surfaced by the caller.
      if (r.status === 401 && token) {
        logout();
        throw new Error("unauthorized");
      }
      return r;
    });
  }

  function newSession() {
    const s = crypto.randomUUID();
    localStorage.setItem("session_id", s);
    setSessionId(s);
    return s;
  }

  function onAuth(t, u) {
    localStorage.setItem("token", t);
    localStorage.setItem("username", u);
    localStorage.removeItem("guest_id"); // becoming a real user ends any guest session
    localStorage.removeItem("checkout_token");
    checkoutTokenRef.current = "";
    setGuestId("");
    newSession(); // fresh chat per login (also isolates a new user on a shared browser)
    setUsername(u);
    setToken(t);
  }

  // "Continue to chat" on the login screen: browse anonymously with a random guest id.
  function startGuest() {
    const g = crypto.randomUUID();
    localStorage.setItem("guest_id", g);
    localStorage.removeItem("checkout_token"); // a fresh guest carries no prior checkout auth
    checkoutTokenRef.current = "";
    newSession();
    setGuestId(g);
  }

  function logout() {
    Object.keys(localStorage).filter((k) => k.startsWith("chat:")).forEach((k) => localStorage.removeItem(k));
    localStorage.removeItem("token");
    localStorage.removeItem("username");
    localStorage.removeItem("guest_id");
    localStorage.removeItem("session_id");
    localStorage.removeItem("checkout_token");
    checkoutTokenRef.current = "";
    setToken("");
    setGuestId("");
    setMessages([]);
    setCart(EMPTY_CART);
    setCartOpen(false);
    setPending(null);
  }

  // New chat: switch to a fresh session; the load effect below registers it,
  // rebuilds the greeting + (empty) history, and refreshes the list. Cart kept.
  function newChat() {
    if (loading) return;
    newSession();
    setSessionsOpen(false);
  }

  function switchSession(id) {
    if (loading || id === sessionId) return setSessionsOpen(false);
    localStorage.setItem("session_id", id);
    setSessionId(id); // load effect fetches this chat's history
    setSessionsOpen(false);
  }

  function deleteChat(id) {
    if (loading) return;
    setSessions((ss) => ss.filter((s) => s.session_id !== id)); // optimistic
    localStorage.removeItem(chatKey(id)); // drop its transcript snapshot too
    authFetch(`/sessions/${id}`, { method: "DELETE" }).catch(refreshSessions);
    if (id === sessionId) newSession(); // deleted the open chat → start a fresh one
  }

  useEffect(() => {
    if (!token && !guestId) return;
    (async () => {
      hydratedRef.current = null; // pause snapshot saves until this session's messages are loaded
      // 1. Greeting (with live product count).
      let greeting;
      try {
        const d = await fetch(`${API_BASE}/health`).then((r) => r.json());
        setStatus({ online: true });
        const n = d.product_count;
        if (typeof n === "number") setProductCount(n);
        const count = typeof n === "number" ? n.toLocaleString() : "lots of";
        greeting = {
          role: "bot",
          text: `👋 Hi! I'm your AI shopping assistant. I've got **${count} live products** loaded from the store — what are you looking for today?`,
        };
      } catch {
        setStatus({ online: false });
        greeting = {
          role: "bot",
          text: "👋 Hi! I'm your shopping assistant — but I can't reach the store right now.",
        };
      }
      // 2. Register the session in the JWT-only sidebar list.
      if (token) {
        authFetch("/sessions", {
          method: "POST",
          body: JSON.stringify({ session_id: sessionId }),
        })
          .then(refreshSessions)
          .catch(() => {});
      }
      // 3. Restore history. A localStorage snapshot (full cards + checkout) wins; else
      // fall back to Mongo /allmessage (JWT-only, text-only); else just the greeting.
      const snapshot = restoreSnapshot(sessionId, greeting);
      if (snapshot) {
        setMessages(snapshot);
        setPending(pendingFromMessages(snapshot)); // resume a mid-checkout refresh (re-gate composer)
      } else if (token) {
        try {
          const data = await authFetch(`/allmessage?session_id=${sessionId}`).then((r) => r.json());
          const history = (data.messages || []).map((m) => ({
            role: m.role === "assistant" ? "bot" : "user",
            text: m.content,
          }));
          setMessages([greeting, ...history]);
        } catch {
          setMessages([greeting]);
        }
      } else {
        setMessages([greeting]);
      }
      hydratedRef.current = sessionId; // messages now belong to this session → saves may resume
      // 4. Cart (guest cart via X-Guest-Id, or the customer cart via JWT).
      authFetch("/cart")
        .then((r) => r.json())
        .then(setCart)
        .catch(() => {});
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, guestId, sessionId]);

  // Snapshot the live transcript per session. Gated on hydratedRef so a session
  // switch (sessionId changed, messages not yet reloaded) can't clobber the new key.
  useEffect(() => {
    if (hydratedRef.current !== sessionId || !messages.length) return;
    try {
      localStorage.setItem(chatKey(sessionId), JSON.stringify(messages));
    } catch {
      /* quota/serialization — snapshot is best-effort */
    }
  }, [messages, sessionId]);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  async function send(text) {
    const message = text.trim();
    if (!message || loading) return;
    setMessages((m) => [...m, { role: "user", text: message }]);
    setInput("");
    setLoading(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const res = await authFetch("/chat", {
        method: "POST",
        body: JSON.stringify({ message, session_id: sessionId }),
        signal: ctrl.signal,
      });
      const data = await res.json();
      setMessages((m) => [
        ...m,
        { role: "bot", text: data.reply || "No reply.", products: data.products || [], cartAdded: data.cart_added, suggestions: data.suggestions || [] },
      ]);
      if (data.cart) setCart(data.cart);
      refreshSessions(); // first message may have just AI-named this chat
    } catch (e) {
      if (e.name === "AbortError") return; // user hit stop — leave the chat as-is
      setMessages((m) => [
        ...m,
        { role: "bot", text: "I couldn't reach the shop right now. Is the backend running?" },
      ]);
    } finally {
      abortRef.current = null;
      setLoading(false);
    }
  }
  const stopGenerating = () => abortRef.current?.abort();

  async function addToCart(sku, qty = 1, name) {
    try {
      const res = await authFetch("/cart/add", {
        method: "POST",
        body: JSON.stringify({ sku, qty }),
      });
      const data = await res.json();
      if (data.cart) setCart(data.cart);
      // Card-button adds bypass the LLM, so nudge with the same next-step buttons.
      if (data.ok) {
        setMessages((m) => [
          ...m,
          { role: "bot", text: `✅ Added ${qty} × ${name || sku} to your cart. What would you like next?`, cartAdded: true },
        ]);
      }
      return data.ok;
    } catch {
      return false;
    }
  }

  // A next-step button was clicked: just append the reply. Buttons stay put
  // (visibility is driven by the message's cartAdded flag, which we leave on).
  function replyToAction(reply) {
    setMessages((m) => [...m, { role: "bot", text: reply }]);
  }

  const bot = (msg) => setMessages((m) => [...m, { role: "bot", ...msg }]);
  const user = (text) => setMessages((m) => [...m, { role: "user", text }]);
  // Lock a resolved picker so its buttons can't be re-triggered from history.
  const markDone = (idx) => setMessages((m) => m.map((msg, i) => (i === idx ? { ...msg, done: true } : msg)));

  // Step 0 (guests only) — email-first: ask for the email, then branch on whether it's
  // a known account (→ password) or not (→ guest checkout).
  async function startCheckout() {
    if (loading || pending) return;
    setCartOpen(false);
    if (!token && !checkoutTokenRef.current) {
      pwAttemptsRef.current = 0;
      setPending("email");
      bot({ kind: "emailForm", text: "Great! What email should we use for your order? 👋" });
      return;
    }
    showAddressPicker();
  }

  // Email entered: does this email belong to an account? Known → ask for the password;
  // unknown → continue as a guest (real guest order).
  async function checkEmail(email, idx) {
    markDone(idx);
    setCheckoutEmail(email);
    setLoading(true);
    try {
      const { exists } = await fetch(`${API_BASE}/auth/check-email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      }).then((r) => r.json());
      if (exists) {
        setPending("login");
        bot({ kind: "passwordForm", email, text: `Welcome back! Enter the password for **${email}** — or continue as a guest.` });
      } else {
        bot({ text: `No account found for **${email}** — continuing as a guest 👍` });
        startGuestAddress(email);
      }
    } catch {
      bot({ text: "Can't reach the server. Is the backend running?" });
      setPending(null);
    } finally {
      setLoading(false);
    }
  }

  // Guest checkout: skip the saved-address picker (guests have none) and collect one
  // address inline. authFetch carries X-Guest-Id, so /checkout/quote|place hit the guest path.
  function startGuestAddress(email) {
    setPending("address");
    bot({ kind: "addressForm", guest: true, email, text: "Where should we deliver your order? 📦" });
  }

  // "Continue as guest" tapped on the email/password card: skip auth, go straight to guest checkout.
  function guestCheckout(email, idx) {
    markDone(idx);
    startGuestAddress(email || checkoutEmail);
  }

  // Password entered for a known account: authenticate, merge the guest cart, continue.
  // Wrong password → retry (with a guest escape) up to MAX_PW_ATTEMPTS, then force guest.
  async function checkoutLogin({ password }, idx) {
    markDone(idx);
    const email = checkoutEmail;
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (!res.ok) {
        pwAttemptsRef.current += 1;
        if (pwAttemptsRef.current >= MAX_PW_ATTEMPTS) {
          bot({ text: "That didn't match. Continuing as a guest so you can still check out 👍" });
          startGuestAddress(email);
        } else {
          const left = MAX_PW_ATTEMPTS - pwAttemptsRef.current;
          setPending("login");
          bot({ kind: "passwordForm", email, text: `That password didn't match — ${left} ${left === 1 ? "try" : "tries"} left. Try again, or continue as a guest.` });
        }
        return;
      }
      // Hold the token in the ref (not `token` state) so we don't reload the chat;
      // persist it so a refresh mid-checkout keeps this authed identity (& cart).
      checkoutTokenRef.current = data.token;
      localStorage.setItem("checkout_token", data.token);
      setUsername(data.username);
      user(`Signed in as ${data.username} ✅`);
      try {
        const merged = await fetch(`${API_BASE}/cart/merge`, {
          method: "POST",
          headers: { "Content-Type": "application/json",
                     Authorization: `Bearer ${data.token}`, "X-Guest-Id": guestId },
        }).then((r) => r.json());
        if (merged.cart) setCart(merged.cart);
      } catch {
        /* merge is best-effort; the customer cart is still usable */
      }
      showAddressPicker();
    } catch {
      bot({ text: "Can't reach the server. Is the backend running?" });
    } finally {
      setLoading(false);
    }
  }

  // Step 1 — show the saved-address picker (authFetch now carries the checkout token).
  async function showAddressPicker() {
    setPending("address");
    let addresses = [];
    try {
      addresses = await authFetch("/checkout/addresses").then((r) => r.json());
    } catch {
      /* show the picker empty → Add New still works */
    }
    bot({ kind: "addressPicker", addresses, text: "Where should we deliver your order? 📦" });
  }

  // Saved address picked: lock the list and ask the user to confirm before quoting
  // (mirrors the guest review step). "Change" on the confirm card re-lists the addresses.
  function selectSavedAddress(addr, idx) {
    markDone(idx);
    bot({ kind: "addressConfirm", addr, saved: true, text: "📍 Please confirm your delivery address:" });
  }

  // Guest checkout: review the full address + email before quoting. The payment card
  // only echoes the city ("Delivering to: MH"), so confirm the whole thing first.
  function confirmAddress(form, idx) {
    markDone(idx);
    bot({ kind: "addressConfirm", addr: form, text: "Please review your delivery details:" });
  }

  // "Change" on the confirm card. Saved-address (customer) → re-show the saved list;
  // guest → re-open the form prefilled so the next submit overwrites.
  function editAddress(addr, idx, saved) {
    markDone(idx);
    if (saved) { showAddressPicker(); return; }
    bot({ kind: "addressForm", guest: true, email: addr.email, initial: addr, text: "Update your delivery details:" });
  }

  // Step 2 — address confirmed: quote shipping, then show the payment picker. Locks the
  // confirm card that triggered it (guest review card or customer saved-address confirm).
  async function chooseAddress(addr, idx) {
    markDone(idx);
    user(`Deliver to: ${addrLine(addr)} ${addrIcon(addr.label)}`);
    setLoading(true);
    try {
      const data = await authFetch("/checkout/quote", { method: "POST", body: JSON.stringify(addr) }).then((r) => r.json());
      if (!data.ok) {
        bot({ text: data.error || "Couldn't get shipping options." });
        setPending(null);
        return;
      }
      setPending("payment");
      bot({
        kind: "paymentPicker",
        methods: data.payment_methods || [],
        total: data.totals?.grand_total ?? null,
        text: `📍 Delivering to: **${addrLine(addr)}**\n\nHow would you like to pay?`,
      });
    } catch {
      bot({ text: "Something went wrong getting shipping options." });
      setPending(null);
    } finally {
      setLoading(false);
    }
  }

  // Add New Address tapped: lock the picker and drop an inline address form.
  function openAddressForm(idx) {
    markDone(idx);
    bot({ kind: "addressForm", text: "Add a new delivery address:" });
  }

  // Add New Address submitted: persist it, then re-show the picker with the fuller list.
  async function addAddress(form, idx) {
    markDone(idx);
    setLoading(true);
    try {
      const addresses = await authFetch("/checkout/addresses", { method: "POST", body: JSON.stringify(form) }).then((r) => r.json());
      bot({ kind: "addressPicker", addresses, text: "Saved ✅ — where should we deliver?" });
    } catch {
      bot({ text: "Couldn't save that address. Please try again." });
      setPending(null);
    } finally {
      setLoading(false);
    }
  }

  // Step 3 — payment chosen: place the order, drop the confirmation card in chat.
  async function choosePayment(method, idx) {
    markDone(idx);
    user(`Pay with ${method.title || method.code}`);
    setPending(null);
    setLoading(true);
    try {
      const data = await authFetch("/checkout/place", {
        method: "POST",
        body: JSON.stringify({ payment_method_code: method.code }),
      }).then((r) => r.json());
      if (!data.ok) {
        bot({ text: data.error || "Order failed. Please try again." });
        return;
      }
      setCart(EMPTY_CART);
      // Order placed → lock every checkout picker so the address can't be re-quoted
      // against the now-empty cart.
      setMessages((m) => m.map((msg) =>
        (msg.kind === "addressPicker" || msg.kind === "paymentPicker") ? { ...msg, done: true } : msg));
      bot({ text: "", order: data, speakText: orderSpeech(data) });
    } catch {
      bot({ text: "Order failed. Please try again." });
    } finally {
      setLoading(false);
    }
  }

  // Re-pull the cart from the store (customer /carts/mine, or the guest cart) — lets the
  // user reconcile the panel with items they changed on the Magento site directly.
  async function syncCart() {
    const data = await authFetch("/cart").then((r) => r.json());
    setCart(data);
    return data;
  }

  async function removeFromCart(itemId) {
    try {
      const res = await authFetch("/cart/remove", {
        method: "POST",
        body: JSON.stringify({ item_id: itemId }),
      });
      const data = await res.json();
      if (data.cart) setCart(data.cart);
    } catch {
      /* logged out */
    }
  }

  if (!token && !guestId) return <Auth onAuth={onAuth} onGuest={startGuest} />;

  const isGuest = !token; // browsing anonymously (a checkout token may be held in the ref)

  const showChips = messages.length <= 1 && !loading;

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar__inner">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true">🧺</span>
          <span className="brand__name">Grocerzy</span>
          <span className="brand__tag">shopping assistant</span>
        </div>
        <div className="topbar__right">
          <div className={`status status--${status.online === false ? "off" : "on"}`}>
            <span className="status__dot" />
            {status.online === false
              ? "offline"
              : productCount != null
                ? `Live · ${productCount.toLocaleString()} products`
                : status.online
                  ? "Live"
                  : "connecting…"}
          </div>
          {!isGuest && (
            <button className="cartbtn" onClick={() => setSessionsOpen(true)} aria-label="Show chats">
              <span className="cartbtn__icon" aria-hidden="true">☰</span>
              Chats
            </button>
          )}
          <button className="cartbtn" onClick={newChat} disabled={loading} aria-label="Start a new chat">
            <span className="cartbtn__icon" aria-hidden="true">✎</span>
            New chat
          </button>
          {tts.supported && (
            <button
              className="cartbtn"
              onClick={tts.toggle}
              aria-label={tts.enabled ? "Mute spoken replies" : "Speak replies aloud"}
              title={tts.enabled ? "Mute spoken replies" : "Speak replies aloud"}
            >
              <span className="cartbtn__icon" aria-hidden="true">{tts.enabled ? "🔊" : "🔇"}</span>
              {tts.enabled ? "Voice on" : "Voice off"}
            </button>
          )}
          <button className="cartbtn" onClick={() => setCartOpen(true)} aria-label="Open cart">
            <span className="cartbtn__icon" aria-hidden="true">🛒</span>
            Cart
            {cart.items_qty > 0 && <span className="cartbtn__badge">{cart.items_qty}</span>}
          </button>
          {isGuest ? (
            <button className="cartbtn" onClick={logout} aria-label="Sign in">
              <span className="cartbtn__icon" aria-hidden="true">👤</span>
              Sign in
            </button>
          ) : (
            <div className="usermenu">
              <button className="userbtn" aria-label="Account menu">
                {username?.[0]?.toUpperCase() || "?"}
              </button>
              <div className="usermenu__pop">
                <div className="usermenu__name">
                  Signed in as <b>{username}</b>
                </div>
                <button className="usermenu__logout" onClick={logout}>
                  ↩ Log out
                </button>
              </div>
            </div>
          )}
        </div>
        </div>
      </header>

      <div className="shell">
      <div className="grain" aria-hidden="true" />

      <main className={`log ${showChips ? "log--intro" : ""}`} ref={logRef}>
        <div className="thread">
          {messages.map((m, i) => (
            <Message
              key={i}
              idx={i}
              {...m}
              onAdd={addToCart}
              onSelect={send}
              authFetch={authFetch}
              onContinue={() => replyToAction("What else would you like to add? 🛍️")}
              onCheckout={startCheckout}
              onShopAgain={() => { setCartOpen(false); newChat(); }}
              onChooseAddress={selectSavedAddress}
              onAddNew={openAddressForm}
              onSubmitAddress={(f, idx) => (!token && !checkoutTokenRef.current ? confirmAddress(f, idx) : addAddress(f, idx))}
              onConfirmAddress={chooseAddress}
              onEditAddress={editAddress}
              onChoosePayment={choosePayment}
              onCheckEmail={checkEmail}
              onGuestCheckout={guestCheckout}
              onCheckoutLogin={checkoutLogin}
              canInvoice={!!(token || checkoutTokenRef.current)}
            />
          ))}
          {loading && (
            <div className="row row--bot">
              <Avatar role="bot" />
              <div className="bubble bubble--bot typing">
                <span /><span /><span />
              </div>
            </div>
          )}
        </div>

        {showChips && (
          <div className="tags">
            {SUGGESTIONS.map((s) => (
              <button key={s.text} className="tag" onClick={() => send(s.text)}>
                <span className="tag__emoji">{s.emoji}</span>
                {s.text}
              </button>
            ))}
          </div>
        )}
      </main>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        {voice === "off" ? (
          <>
            <div className="composer__field">
              <input
                className="composer__input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={pending ? "Use the buttons above to continue…" : "Ask for a product…"}
                disabled={!!pending}
                autoFocus
              />
              {speech.supported && (
                <button
                  type="button"
                  className="composer__mic"
                  onClick={startVoice}
                  disabled={!!pending}
                  aria-label="Start voice input"
                  title={speech.error || "Start voice input"}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="9" y="2" width="6" height="12" rx="3" />
                    <path d="M5 10v1a7 7 0 0 0 14 0v-1" />
                    <line x1="12" y1="18" x2="12" y2="22" />
                  </svg>
                </button>
              )}
            </div>
            {loading ? (
              <button
                type="button"
                className="composer__send composer__send--stop"
                onClick={stopGenerating}
                aria-label="Stop generating"
                title="Stop"
              >
                <span className="composer__stopicon" aria-hidden="true" />
              </button>
            ) : (
              <button className="composer__send" type="submit" disabled={!!pending || !input.trim()}>
                Send
              </button>
            )}
          </>
        ) : (
          <div className="voicebar">
            <VoiceWave onSilence={silentSend} />
            <button
              type="button"
              className="voicebar__btn voicebar__btn--cancel"
              onClick={cancelVoice}
              aria-label="Cancel voice input"
              title="Cancel"
            >
              ✕
            </button>
            <button
              type="button"
              className="voicebar__btn voicebar__btn--confirm"
              onClick={confirmVoice}
              disabled={voice === "loading"}
              aria-label="Use transcript"
              title="Done"
            >
              {voice === "loading" ? <span className="voicebar__spin" aria-label="Transcribing" /> : "✓"}
            </button>
          </div>
        )}
      </form>

      <CartPanel
        cart={cart}
        open={cartOpen}
        onClose={() => setCartOpen(false)}
        onRemove={removeFromCart}
        onCheckout={startCheckout}
        onSync={syncCart}
      />

      <SessionsPanel
        sessions={sessions}
        activeId={sessionId}
        open={sessionsOpen}
        onClose={() => setSessionsOpen(false)}
        onPick={switchSession}
        onNew={newChat}
        onDelete={deleteChat}
      />
      </div>
    </div>
  );
}

function SessionsPanel({ sessions, activeId, open, onClose, onPick, onNew, onDelete }) {
  return (
    <>
      <div className={`overlay ${open ? "overlay--on" : ""}`} onClick={onClose} />
      <aside className={`sessions ${open ? "sessions--open" : ""}`} aria-hidden={!open}>
        <div className="sessions__head">
          <h2 className="sessions__title">Your chats</h2>
          <button className="cart__close" onClick={onClose} aria-label="Close chats">✕</button>
        </div>
        <button className="sessions__new" onClick={onNew}>＋ New chat</button>
        <div className="sessions__list">
          {sessions.length === 0 ? (
            <p className="muted" style={{ padding: "8px 12px" }}>No chats yet.</p>
          ) : (
            sessions.map((s) => (
              <div
                key={s.session_id}
                className={`sessitem ${s.session_id === activeId ? "sessitem--active" : ""}`}
              >
                <button
                  className="sessitem__name"
                  onClick={() => onPick(s.session_id)}
                  title={s.session_name}
                >
                  {s.session_name || "New chat"}
                </button>
                <button
                  className="sessitem__del"
                  onClick={() => onDelete(s.session_id)}
                  aria-label="Delete chat"
                  title="Delete chat"
                >
                  🗑
                </button>
              </div>
            ))
          )}
        </div>
      </aside>
    </>
  );
}

function CartPanel({ cart, open, onClose, onRemove, onCheckout, onSync }) {
  const [syncing, setSyncing] = useState(false);
  const [note, setNote] = useState("");
  async function handleSync() {
    setSyncing(true);
    try {
      const data = await onSync();
      const n = data?.items_qty ?? 0;
      setNote(`✓ ${n} item${n === 1 ? "" : "s"} retrieved successfully`);
      setTimeout(() => setNote(""), 3000);
    } catch {
      setNote("Couldn't sync your cart. Try again.");
      setTimeout(() => setNote(""), 3000);
    } finally {
      setSyncing(false);
    }
  }
  return (
    <>
      <div className={`overlay ${open ? "overlay--on" : ""}`} onClick={onClose} />
      <aside className={`cart ${open ? "cart--open" : ""}`} aria-hidden={!open}>
        <div className="cart__head">
          <h2 className="cart__title">Your cart</h2>
          <div className="cart__head-actions">
            <button className="cart__sync" onClick={handleSync} disabled={syncing}>
              {syncing ? "Syncing…" : "⟳ Sync cart"}
            </button>
            <button className="cart__close" onClick={onClose} aria-label="Close cart">✕</button>
          </div>
        </div>
        {note && <p className="cart__note" role="status">{note}</p>}

        {cart.items.length === 0 ? (
          <div className="cart__empty">
            <span>🛒</span>
            <p>Your cart is empty.</p>
            <p className="muted">Ask the assistant or tap “Add” on a product.</p>
          </div>
        ) : (
          <div className="cart__items">
            {cart.items.map((it) => (
              <div className="citem" key={it.item_id}>
                <div className="citem__thumb">
                  {it.image ? <img src={it.image} alt="" loading="lazy" /> : <span>🛍️</span>}
                </div>
                <div className="citem__body">
                  <p className="citem__name" title={it.name}>{it.name}</p>
                  <p className="citem__meta">Qty {Number(it.qty)} · {money(it.price)}</p>
                </div>
                <button
                  className="citem__rm"
                  onClick={() => onRemove(it.item_id)}
                  aria-label="Remove item"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="cart__foot">
          <div className="cart__total">
            <span>Total</span>
            <strong>{money(cart.grand_total)}</strong>
          </div>
          <button
            className="cart__checkout"
            onClick={onCheckout}
            disabled={cart.items.length === 0}
          >
            Proceed to checkout →
          </button>
        </div>
      </aside>
    </>
  );
}

function Message({
  role, text, products, suggestions, order, kind, addresses, methods, total, done, idx, email, addr, initial, saved,
  onAdd, onSelect, authFetch, cartAdded, onContinue, onCheckout, onShopAgain,
  onChooseAddress, onAddNew, onSubmitAddress, onConfirmAddress, onEditAddress, onChoosePayment, onCheckEmail, onGuestCheckout, onCheckoutLogin, canInvoice,
}) {
  return (
    <div className={`row row--${role}`}>
      <Avatar role={role} />
      <div className={`bubble bubble--${role}`}>
        {role === "bot" ? (
          <>
            {text && (
              <div className="md">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
              </div>
            )}
            {kind === "addressPicker" && (
              <AddressPicker addresses={addresses} done={done} onChoose={(a) => onChooseAddress(a, idx)} onAddNew={() => onAddNew(idx)} />
            )}
            {kind === "paymentPicker" && (
              <PaymentPicker methods={methods} total={total} done={done} onChoose={(mth) => onChoosePayment(mth, idx)} />
            )}
            {kind === "addressForm" && (
              <AddressForm done={done} email={email} initial={initial} onSubmit={(f) => onSubmitAddress(f, idx)} />
            )}
            {kind === "addressConfirm" && (
              <AddressConfirm addr={addr} done={done}
                onConfirm={() => onConfirmAddress(addr, idx)} onEdit={() => onEditAddress(addr, idx, saved)} />
            )}
            {kind === "emailForm" && (
              <CheckoutEmail done={done} onSubmit={(e) => onCheckEmail(e, idx)} onGuest={(e) => onGuestCheckout(e, idx)} />
            )}
            {kind === "passwordForm" && (
              <CheckoutPassword done={done} email={email} onSubmit={(c) => onCheckoutLogin(c, idx)} onGuest={() => onGuestCheckout(email, idx)} />
            )}
            {order && <OrderCard order={order} authFetch={authFetch} onShopAgain={onShopAgain} canInvoice={canInvoice} />}
            {cartAdded && (
              <div className="msg-actions">
                <button className="msg-actions__btn" onClick={onContinue}>
                  🛍️ Continue shopping
                </button>
                <button className="msg-actions__btn msg-actions__btn--primary" onClick={onCheckout}>
                  Proceed to checkout →
                </button>
              </div>
            )}
            {suggestions?.length > 0 && (
              <div className="tags tags--inline">
                {suggestions.map((s) => (
                  <button key={s} className="tag" onClick={() => onSelect(s)}>
                    {s}
                  </button>
                ))}
              </div>
            )}
            {products?.length > 0 && (
              <div className="cards">
                {products.map((p) => (
                  <ProductCard key={p.sku} product={p} onAdd={onAdd} />
                ))}
              </div>
            )}
          </>
        ) : (
          text
        )}
      </div>
    </div>
  );
}

function ProductCard({ product, onAdd }) {
  const [broken, setBroken] = useState(false);
  const [state, setState] = useState("idle"); // idle | selecting | adding | added
  const [qty, setQty] = useState(1);
  const price = product.price != null ? money(product.price) : "—";

  async function handleAdd() {
    setState("adding");
    const ok = await onAdd(product.sku, qty, product.name);
    setState(ok ? "added" : "selecting");
    if (ok) setTimeout(() => { setState("idle"); setQty(1); }, 1600);
  }

  return (
    <article className="card">
      <div className="card__thumb">
        {product.image && !broken ? (
          <img
            src={product.image}
            alt={product.name || product.sku}
            loading="lazy"
            onError={() => setBroken(true)}
          />
        ) : (
          <span className="card__ph" aria-hidden="true">🛍️</span>
        )}
      </div>
      <div className="card__body">
        <p className="card__name" title={product.name}>{product.name || product.sku}</p>
        <div className="card__foot">
          <span className="card__price">{price}</span>
          <span className="card__sku">{product.sku}</span>
        </div>
        {state === "idle" ? (
          <button className="card__add" onClick={() => setState("selecting")}>
            ＋ Add
          </button>
        ) : (
          <>
            {(state === "selecting" || state === "adding") && (
              <div className="card__qty">
                <button
                  className="card__qty-btn"
                  onClick={() => setQty((q) => Math.max(1, q - 1))}
                  disabled={qty <= 1 || state === "adding"}
                  aria-label="Decrease quantity"
                >
                  −
                </button>
                <span className="card__qty-num">{qty}</span>
                <button
                  className="card__qty-btn"
                  onClick={() => setQty((q) => q + 1)}
                  disabled={state === "adding"}
                  aria-label="Increase quantity"
                >
                  ＋
                </button>
              </div>
            )}
            <button
              className={`card__add card__add--${state}`}
              onClick={handleAdd}
              disabled={state === "adding"}
            >
              {state === "added"
                ? "✓ Added"
                : state === "adding"
                ? "Adding…"
                : `Add ${qty} to cart`}
            </button>
          </>
        )}
      </div>
    </article>
  );
}

function OrderCard({ order, authFetch, onShopAgain, canInvoice = true }) {
  const [busy, setBusy] = useState(false);

  async function viewInvoice() {
    setBusy(true);
    try {
      const res = await authFetch(`/checkout/invoice/${order.order_id}`);
      const blob = await res.blob(); // authFetch keeps the JWT in the header; a plain <a> can't
      window.open(URL.createObjectURL(blob), "_blank");
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="order">
      <div className="order__hero" aria-hidden="true">🎉</div>
      <h3 className="order__title">Order Placed Successfully!</h3>
      <p className="order__id">Order ID: <b>{order.order_increment_id}</b></p>

      <div className="order__items">
        {order.items?.map((it, i) => (
          <div className="order__line" key={i}>
            <span className="order__lname">
              {it.name} <span className="order__qty">×{it.qty}</span>
            </span>
            <span className="order__lprice">{money(it.row_total)}</span>
          </div>
        ))}
        <div className="order__line order__line--total">
          <span>Total Paid</span>
          <strong>{money(order.total)}</strong>
        </div>
      </div>

      {order.ship_to && <p className="order__meta">📦 Ship to: <b>{order.ship_to}</b></p>}
      {order.delivery_by && <p className="order__meta">🚚 Delivery by: <b>{order.delivery_by}</b></p>}
      <p className="order__meta">🧾 Invoice emailed to your registered email</p>

      <div className="order__actions">
        {canInvoice && (
          <button className="order__btn order__btn--primary" onClick={viewInvoice} disabled={busy}>
            {busy ? "…" : "👁 View Invoice"}
          </button>
        )}
        <button className="order__btn" onClick={onShopAgain}>🛍️ Shop Again</button>
      </div>
    </div>
  );
}

const EMPTY_FORM = { name: "", email: "", phone: "", street: "", city: "", region: "", postcode: "" };

// In-chat checkout step 1: pick a saved address or add a new one.
function AddressPicker({ addresses = [], done, onChoose, onAddNew }) {
  return (
    <div className="picker">
      {addresses.map((a, i) => (
        <button key={i} className="addr" disabled={done} onClick={() => onChoose(a)}>
          <span className="addr__icon" aria-hidden="true">{addrIcon(a.label)}</span>
          <span className="addr__body">
            <span className="addr__label">{a.street || a.city || "Address"}</span>
            <span className="addr__line">
              {[a.city, a.region].filter(Boolean).join(", ")}
              {a.postcode ? ` – ${a.postcode}` : ""}
            </span>
            {a.phone && <span className="addr__line">{a.phone}</span>}
          </span>
        </button>
      ))}
      <button className="picker__add" disabled={done} onClick={onAddNew}>＋ Add New Address</button>
    </div>
  );
}

// In-chat checkout step 2: pick one of the store's real Magento payment methods.
function PaymentPicker({ methods = [], total, done, onChoose }) {
  return (
    <div className="picker">
      {methods.length === 0 && <p className="cform__err">No payment methods available for this store.</p>}
      {methods.map((m) => (
        <button key={m.code} className="pay" disabled={done} onClick={() => onChoose(m)}>
          <span className="pay__icon" aria-hidden="true">{payIcon(m.code)}</span>
          <span className="pay__title">{m.title || m.code}</span>
        </button>
      ))}
      {total != null && (
        <div className="cart__total"><span>Total</span><strong>{money(total)}</strong></div>
      )}
    </div>
  );
}

// Inline "Add New Address" form; collapses once submitted (parent marks it done).
// `email` prefills the field for guests, who already gave it at the email step.
function AddressForm({ done, email, initial, onSubmit }) {
  const [form, setForm] = useState(() => ({ ...EMPTY_FORM, ...(initial || {}), email: (initial?.email ?? email) || "" }));
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  if (done) return null;
  return (
    <form className="cform" onSubmit={(e) => { e.preventDefault(); onSubmit(form); }}>
      <input className="auth__input" placeholder="Full name" value={form.name} onChange={set("name")} required />
      <input className="auth__input" type="email" placeholder="Email" value={form.email} onChange={set("email")} required />
      <input className="auth__input" placeholder="Phone" value={form.phone} onChange={set("phone")} required />
      <input className="auth__input" placeholder="Street address" value={form.street} onChange={set("street")} required />
      <input className="auth__input" placeholder="City" value={form.city} onChange={set("city")} required />
      <input className="auth__input" placeholder="Region / Governorate" value={form.region} onChange={set("region")} />
      <input className="auth__input" placeholder="Postcode" value={form.postcode} onChange={set("postcode")} required />
      <button className="cart__checkout" type="submit">Save address</button>
    </form>
  );
}

// Guest checkout: full delivery summary + confirm/change, shown before we quote shipping.
function AddressConfirm({ addr, done, onConfirm, onEdit }) {
  if (done) return null;
  const where = [addr.street, addr.city, addr.region, addr.postcode].filter(Boolean).join(", ");
  return (
    <div className="confirm">
      <div className="confirm__lines">
        <div><strong>{addr.name}</strong></div>
        <div>📍 {where}</div>
        {addr.email && <div>📧 {addr.email}</div>}
        {addr.phone && <div>📞 {addr.phone}</div>}
      </div>
      <p className="confirm__ask">Is this correct?</p>
      <div className="msg-actions">
        <button className="msg-actions__btn msg-actions__btn--primary" onClick={onConfirm}>✓ Yes, continue</button>
        <button className="msg-actions__btn" onClick={onEdit}>✏️ Change</button>
      </div>
    </div>
  );
}

// In-chat checkout step 0a: email only. Parent looks it up → known email asks for a
// password (CheckoutPassword), unknown email goes straight to guest checkout.
function CheckoutEmail({ done, onSubmit, onGuest }) {
  const [email, setEmail] = useState("");
  if (done) return null;
  return (
    <form className="cform" onSubmit={(e) => { e.preventDefault(); onSubmit(email.trim()); }}>
      <input className="auth__input" type="email" placeholder="Enter your email address…"
             value={email} onChange={(e) => setEmail(e.target.value)} required />
      <button className="cart__checkout" type="submit" disabled={!email}>Continue →</button>
      <button type="button" className="auth__toggle" onClick={() => onGuest(email.trim())}>
        👤 Continue as guest
      </button>
    </form>
  );
}

// In-chat checkout step 0b: password for a known account. "Continue as guest" bails to
// the guest path (parent handles the retry limit before ever re-showing this card).
function CheckoutPassword({ done, email, onSubmit, onGuest }) {
  const [password, setPassword] = useState("");
  if (done) return null;
  return (
    <form className="cform" onSubmit={(e) => { e.preventDefault(); onSubmit({ password }); }}>
      <input className="auth__input" type="email" value={email || ""} readOnly tabIndex={-1} />
      <input className="auth__input" type="password" placeholder="Password" autoFocus
             value={password} onChange={(e) => setPassword(e.target.value)} required />
      <button className="cart__checkout" type="submit" disabled={!password}>Continue →</button>
      <button type="button" className="auth__toggle" onClick={onGuest}>
        👤 Continue as guest
      </button>
    </form>
  );
}

function Avatar({ role }) {
  return (
    <div className={`avatar avatar--${role}`} aria-hidden="true">
      {role === "bot" ? "🧺" : "👨"}
    </div>
  );
}
