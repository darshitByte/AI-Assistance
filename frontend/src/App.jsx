import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Auth from "./Auth";
import { API_BASE, CURRENCY } from "./config";

const SUGGESTIONS = [
  { emoji: "🎧", text: "Show me some Sony headphones" },
  { emoji: "🥛", text: "What milk do you have?" },
  { emoji: "💰", text: "Find earbuds under 20" },
];

const money = (n) => `${CURRENCY} ${Number(n || 0).toLocaleString()}`;
const EMPTY_CART = { items: [], items_qty: 0, grand_total: 0, currency: CURRENCY };

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem("token") || "");
  const [username, setUsername] = useState(() => localStorage.getItem("username") || "");
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
  const [sessions, setSessions] = useState([]);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const logRef = useRef(null);

  function refreshSessions() {
    authFetch("/sessions")
      .then((r) => r.json())
      .then((d) => setSessions(d.sessions || []))
      .catch(() => {});
  }

  function authFetch(path, options = {}) {
    return fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...(options.headers || {}),
      },
    }).then((r) => {
      if (r.status === 401) {
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
    newSession(); // fresh chat per login (also isolates a new user on a shared browser)
    setUsername(u);
    setToken(t);
  }

  function logout() {
    localStorage.removeItem("token");
    localStorage.removeItem("username");
    localStorage.removeItem("session_id");
    setToken("");
    setMessages([]);
    setCart(EMPTY_CART);
    setCartOpen(false);
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
    authFetch(`/sessions/${id}`, { method: "DELETE" }).catch(refreshSessions);
    if (id === sessionId) newSession(); // deleted the open chat → start a fresh one
  }

  useEffect(() => {
    if (!token) return;
    (async () => {
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
      // 2. Register this session (idempotent) + refresh the chat list.
      authFetch("/sessions", {
        method: "POST",
        body: JSON.stringify({ session_id: sessionId }),
      })
        .then(refreshSessions)
        .catch(() => {});
      // 3. Restore prior conversation from MongoDB (for the current session).
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
      // 4. Cart.
      authFetch("/cart")
        .then((r) => r.json())
        .then(setCart)
        .catch(() => {});
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, sessionId]);

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
    try {
      const res = await authFetch("/chat", {
        method: "POST",
        body: JSON.stringify({ message, session_id: sessionId }),
      });
      const data = await res.json();
      setMessages((m) => [
        ...m,
        { role: "bot", text: data.reply || "No reply.", products: data.products || [], cartAdded: data.cart_added },
      ]);
      if (data.cart) setCart(data.cart);
      refreshSessions(); // first message may have just AI-named this chat
    } catch {
      setMessages((m) => [
        ...m,
        { role: "bot", text: "I couldn't reach the shop right now. Is the backend running?" },
      ]);
    } finally {
      setLoading(false);
    }
  }

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

  if (!token) return <Auth onAuth={onAuth} />;

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
          <button className="cartbtn" onClick={() => setSessionsOpen(true)} aria-label="Show chats">
            <span className="cartbtn__icon" aria-hidden="true">☰</span>
            Chats
          </button>
          <button className="cartbtn" onClick={newChat} disabled={loading} aria-label="Start a new chat">
            <span className="cartbtn__icon" aria-hidden="true">✎</span>
            New chat
          </button>
          <button className="cartbtn" onClick={() => setCartOpen(true)} aria-label="Open cart">
            <span className="cartbtn__icon" aria-hidden="true">🛒</span>
            Cart
            {cart.items_qty > 0 && <span className="cartbtn__badge">{cart.items_qty}</span>}
          </button>
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
              {...m}
              onAdd={addToCart}
              onContinue={() => replyToAction("What else would you like to add? 🛍️")}
              onCheckout={() => replyToAction("Checkout is coming soon 🛒")}
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
        <input
          className="composer__input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask for a product…"
          autoFocus
        />
        <button className="composer__send" type="submit" disabled={loading || !input.trim()}>
          {loading ? "…" : "Send"}
        </button>
      </form>

      <CartPanel
        cart={cart}
        open={cartOpen}
        onClose={() => setCartOpen(false)}
        onRemove={removeFromCart}
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

function CartPanel({ cart, open, onClose, onRemove }) {
  return (
    <>
      <div className={`overlay ${open ? "overlay--on" : ""}`} onClick={onClose} />
      <aside className={`cart ${open ? "cart--open" : ""}`} aria-hidden={!open}>
        <div className="cart__head">
          <h2 className="cart__title">Your cart</h2>
          <button className="cart__close" onClick={onClose} aria-label="Close cart">✕</button>
        </div>

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
          <button className="cart__checkout" disabled title="Checkout is coming soon">
            Checkout — coming soon
          </button>
        </div>
      </aside>
    </>
  );
}

function Message({ role, text, products, onAdd, cartAdded, onContinue, onCheckout }) {
  return (
    <div className={`row row--${role}`}>
      <Avatar role={role} />
      <div className={`bubble bubble--${role}`}>
        {role === "bot" ? (
          <>
            <div className="md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
            </div>
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

function Avatar({ role }) {
  return (
    <div className={`avatar avatar--${role}`} aria-hidden="true">
      {role === "bot" ? "🧺" : "👨"}
    </div>
  );
}
