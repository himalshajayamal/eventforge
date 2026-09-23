import React from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function App() {
  const [status, setStatus] = React.useState("checking...");

  React.useEffect(() => {
    fetch(`${API_BASE}/health`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => setStatus(data.status))
      .catch(() => setStatus("unreachable"));
  }, []);

  return (
    <main className="shell">
      <section className="card">
        <p className="eyebrow">EVENTFORGE</p>
        <h1>Engineering Foundation</h1>
        <p>
          v0.0 establishes the local platform before HookLedger begins.
        </p>
        <div className="status">
          <span>Control API</span>
          <strong>{status}</strong>
        </div>
      </section>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
