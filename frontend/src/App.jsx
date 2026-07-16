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

const addrIcon = (label = "") => {
  const l = label.toLowerCase();
  if (l.includes("home")) return "🏠";
  if (l.includes("office") || l.includes("work")) return "🏢";
  return "📍";
};
const payIcon = (code = "") => {
  const c = code.toLowerCase();
  if (c.includes("cash") || c.includes("cod")) return "💵";
  if (c.includes("bank") || c.includes("transfer")) return "🏦";
  if (c.includes("check")) return "🧾";
  if (c.includes("paypal")) return "🅿️";
  return "💳";
};

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
  const [pending, setPending] = useState(null); // null | "address" | "payment": in-chat checkout step gating the composer
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

  const bot = (msg) => setMessages((m) => [...m, { role: "bot", ...msg }]);
  const user = (text) => setMessages((m) => [...m, { role: "user", text }]);
  // Lock a resolved picker so its buttons can't be re-triggered from history.
  const markDone = (idx) => setMessages((m) => m.map((msg, i) => (i === idx ? { ...msg, done: true } : msg)));

  // Step 1 — open the in-chat checkout: show the saved-address picker.
  async function startCheckout() {
    if (loading || pending) return;
    setCartOpen(false);
    setPending("address");
    let addresses = [];
    try {
      addresses = await authFetch("/checkout/addresses").then((r) => r.json());
    } catch {
      /* show the picker empty → Add New still works */
    }
    bot({ kind: "addressPicker", addresses, text: "Where should we deliver your order? 📦" });
  }

  // Step 2 — address chosen: quote shipping, then show the payment picker.
  async function chooseAddress(addr, idx) {
    markDone(idx);
    user(`Deliver to: ${addr.label || addr.city} ${addrIcon(addr.label)}`);
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
        text: `📍 Delivering to: **${addr.label || addr.city}**\n\nHow would you like to pay?`,
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
      bot({ text: "", order: data });
    } catch {
      bot({ text: "Order failed. Please try again." });
    } finally {
      setLoading(false);
    }
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
              idx={i}
              {...m}
              onAdd={addToCart}
              authFetch={authFetch}
              onContinue={() => replyToAction("What else would you like to add? 🛍️")}
              onCheckout={startCheckout}
              onShopAgain={() => setCartOpen(false)}
              onChooseAddress={chooseAddress}
              onAddNew={openAddressForm}
              onSubmitAddress={addAddress}
              onChoosePayment={choosePayment}
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
          placeholder={pending ? "Use the buttons above to continue…" : "Ask for a product…"}
          disabled={!!pending}
          autoFocus
        />
        <button className="composer__send" type="submit" disabled={loading || !!pending || !input.trim()}>
          {loading ? "…" : "Send"}
        </button>
      </form>

      <CartPanel
        cart={cart}
        open={cartOpen}
        onClose={() => setCartOpen(false)}
        onRemove={removeFromCart}
        onCheckout={startCheckout}
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

function CartPanel({ cart, open, onClose, onRemove, onCheckout }) {
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
  role, text, products, order, kind, addresses, methods, total, done, idx,
  onAdd, authFetch, cartAdded, onContinue, onCheckout, onShopAgain,
  onChooseAddress, onAddNew, onSubmitAddress, onChoosePayment,
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
              <AddressForm done={done} onSubmit={(f) => onSubmitAddress(f, idx)} />
            )}
            {order && <OrderCard order={order} authFetch={authFetch} onShopAgain={onShopAgain} />}
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

function OrderCard({ order, authFetch, onShopAgain }) {
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
        <button className="order__btn order__btn--primary" onClick={viewInvoice} disabled={busy}>
          {busy ? "…" : "👁 View Invoice"}
        </button>
        <button className="order__btn" onClick={onShopAgain}>🛍️ Shop Again</button>
      </div>
    </div>
  );
}

const EMPTY_FORM = { label: "", name: "", email: "", phone: "", street: "", city: "", region: "", postcode: "" };

// In-chat checkout step 1: pick a saved address or add a new one.
function AddressPicker({ addresses = [], done, onChoose, onAddNew }) {
  return (
    <div className="picker">
      {addresses.map((a, i) => (
        <button key={i} className="addr" disabled={done} onClick={() => onChoose(a)}>
          <span className="addr__icon" aria-hidden="true">{addrIcon(a.label)}</span>
          <span className="addr__body">
            <span className="addr__label">{a.label || "Address"}</span>
            {a.street && <span className="addr__line">{a.street}</span>}
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
function AddressForm({ done, onSubmit }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  if (done) return null;
  return (
    <form className="cform" onSubmit={(e) => { e.preventDefault(); onSubmit(form); }}>
      <input className="auth__input" placeholder="Label (e.g. Home, Office)" value={form.label} onChange={set("label")} required />
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

function Avatar({ role }) {
  return (
    <div className={`avatar avatar--${role}`} aria-hidden="true">
      {role === "bot" ? "🧺" : "👨"}
    </div>
  );
}
