import { useState } from "react";
import { API_BASE } from "./config";

export default function Auth({ onAuth, onGuest }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (!res.ok) {
        setErr(data.detail || "Something went wrong.");
        return;
      }
      onAuth(data.token, data.username);
    } catch {
      setErr("Can't reach the server. Is the backend running?");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <div className="auth__grain" aria-hidden="true" />
      {onGuest && (
        <button
          className="auth__toggle"
          onClick={onGuest}
          style={{ position: "absolute", top: 20, right: 24, width: "auto" }}
        >
          Continue to chat →
        </button>
      )}
      <div className="auth__card">
        <div className="auth__brand">
          <span aria-hidden="true">🧺</span> Grocerzy
        </div>
        <p className="auth__sub">
          {mode === "login" ? "Sign in to start shopping" : "Create your account"}
        </p>
        <form onSubmit={submit} className="auth__form">
          <input
            className="auth__input"
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoFocus
          />
          <input
            className="auth__input"
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {err && <div className="auth__err">{err}</div>}
          <button className="auth__btn" disabled={busy || !email || !password}>
            {busy ? "…" : mode === "login" ? "Sign in" : "Sign up"}
          </button>
        </form>
        <button
          className="auth__toggle"
          onClick={() => {
            setMode(mode === "login" ? "signup" : "login");
            setErr("");
          }}
        >
          {mode === "login"
            ? "New here? Create an account"
            : "Already have an account? Sign in"}
        </button>
        <p className="auth__hint">
          Demo login — <b>demo@grocerzy-poc.com</b> / <b>Demo@123</b>
        </p>
      </div>
    </div>
  );
}
