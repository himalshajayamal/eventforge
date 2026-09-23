import React from "react";
import {
  API_BASE,
  ApiError,
  type ApiKeyRow,
  type AuthState,
  type Endpoint,
  type EventRow,
  type JobRow,
  type Project,
  type ProjectSummary,
  type QueueHealth,
  type ReplayRow,
  type StepRun,
  type WorkflowRow,
  type WorkflowRun,
  type WorkflowVersion,
  apiRequest,
  postJson,
  publicGet,
} from "./api";

type ViewName = "overview" | "hookledger" | "replaydb" | "flowtrace" | "security";

type SecretNotice = {
  title: string;
  rows: Array<{ label: string; value: string }>;
  note?: string;
};

type RootInfo = { name: string; version: string; status: string };

type IngressAttempt = {
  id: string;
  event_id: string | null;
  provider_delivery_id: string | null;
  request_id: string;
  outcome: string;
  response_code: number;
  received_at: string;
};

type ReplaySelection = {
  workflow_name: string;
  version_number: number;
  workflow_id: string;
  workflow_version_id: string;
  created_at: string;
};

function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function shortId(value: string | null | undefined, length = 8): string {
  if (!value) return "—";
  return value.length <= length ? value : `${value.slice(0, length)}…`;
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) return `${error.status}: ${error.detail}`;
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}

function statusClass(status: string): string {
  const value = status.toUpperCase();
  if (["SUCCESS", "ACCEPTED", "READY", "OK"].includes(value)) return "status-good";
  if (["PENDING", "RUNNING", "RETRY_WAIT"].includes(value)) return "status-warn";
  if (["DEAD_LETTERED", "FAILED", "REJECTED_SIGNATURE"].includes(value)) return "status-bad";
  return "status-neutral";
}

function StatusPill({ value }: { value: string }) {
  return <span className={`pill ${statusClass(value)}`}>{value}</span>;
}

function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="banner banner-error">{message}</div>;
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>;
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = React.useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }
  return (
    <button className="button button-ghost button-small" type="button" onClick={copy}>
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function SecretPanel({ notice, onClose }: { notice: SecretNotice; onClose: () => void }) {
  return (
    <section className="secret-panel">
      <div className="secret-head">
        <div>
          <span className="kicker">ONE-TIME SECRET</span>
          <h3>{notice.title}</h3>
        </div>
        <button type="button" className="button button-ghost" onClick={onClose}>Dismiss</button>
      </div>
      <p>{notice.note ?? "Store these values now. EventForge will not show the plaintext secret again."}</p>
      <div className="secret-grid">
        {notice.rows.map((row) => (
          <div className="secret-row" key={row.label}>
            <span>{row.label}</span>
            <code>{row.value}</code>
            <CopyButton value={row.value} />
          </div>
        ))}
      </div>
    </section>
  );
}

function LoginScreen({ root, onLogin }: { root: RootInfo | null; onLogin: (auth: AuthState) => Promise<void> }) {
  const [mode, setMode] = React.useState<"admin" | "project">("admin");
  const [username, setUsername] = React.useState("eventforge");
  const [password, setPassword] = React.useState("");
  const [apiKey, setApiKey] = React.useState("");
  const [projectId, setProjectId] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "admin") {
        await onLogin({ mode: "admin", username: username.trim(), password });
      } else {
        await onLogin({ mode: "project", apiKey: apiKey.trim(), projectId: projectId.trim() });
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-shell">
      <section className="login-card">
        <div className="brand-lockup">
          <div className="brand-mark">EF</div>
          <div>
            <p className="kicker">EVENTFORGE</p>
            <h1>Operations console</h1>
          </div>
        </div>
        <p className="login-copy">
          One dashboard for HookLedger, ReplayDB, and FlowTrace. Credentials stay in memory for this browser tab and are not persisted by the frontend.
        </p>

        <div className="segmented">
          <button type="button" className={mode === "admin" ? "active" : ""} onClick={() => setMode("admin")}>Administrator</button>
          <button type="button" className={mode === "project" ? "active" : ""} onClick={() => setMode("project")}>Project API key</button>
        </div>

        <form className="form-stack" onSubmit={submit}>
          {mode === "admin" ? (
            <>
              <label>
                <span>Username</span>
                <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required />
              </label>
              <label>
                <span>Password</span>
                <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required />
              </label>
            </>
          ) : (
            <>
              <label>
                <span>Project ID</span>
                <input value={projectId} onChange={(event) => setProjectId(event.target.value)} placeholder="UUID" required />
              </label>
              <label>
                <span>API key</span>
                <input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="efk_…" required />
              </label>
            </>
          )}
          <ErrorBanner message={error} />
          <button className="button button-primary button-wide" disabled={busy} type="submit">
            {busy ? "Connecting…" : "Connect to EventForge"}
          </button>
        </form>

        <div className="login-meta">
          <span>{root ? `${root.name} ${root.version}` : "Checking API…"}</span>
          <span>{API_BASE}</span>
        </div>
      </section>
    </div>
  );
}

function PageHeader({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description: string; actions?: React.ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <p className="kicker">{eyebrow}</p>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {actions ? <div className="header-actions">{actions}</div> : null}
    </header>
  );
}

function Overview({ auth, projectId }: { auth: AuthState; projectId: string }) {
  const [summary, setSummary] = React.useState<ProjectSummary | null>(null);
  const [events, setEvents] = React.useState<EventRow[]>([]);
  const [jobs, setJobs] = React.useState<JobRow[]>([]);
  const [replays, setReplays] = React.useState<ReplayRow[]>([]);
  const [workflows, setWorkflows] = React.useState<WorkflowRow[]>([]);
  const [runs, setRuns] = React.useState<WorkflowRun[]>([]);
  const [queueHealth, setQueueHealth] = React.useState<QueueHealth | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [summaryValue, eventValue, jobValue, replayValue, workflowValue, runValue, queueHealthValue] = await Promise.all([
        apiRequest<ProjectSummary>(auth, `/projects/${projectId}/summary`),
        apiRequest<EventRow[]>(auth, `/projects/${projectId}/events?limit=8`),
        apiRequest<JobRow[]>(auth, `/projects/${projectId}/jobs?limit=8`),
        apiRequest<ReplayRow[]>(auth, `/projects/${projectId}/replays?limit=8`),
        apiRequest<WorkflowRow[]>(auth, `/projects/${projectId}/workflows`),
        apiRequest<WorkflowRun[]>(auth, `/projects/${projectId}/workflow-runs?limit=8`),
        apiRequest<QueueHealth>(auth, `/projects/${projectId}/queue-health`),
      ]);
      setSummary(summaryValue);
      setEvents(eventValue);
      setJobs(jobValue);
      setReplays(replayValue);
      setWorkflows(workflowValue);
      setRuns(runValue);
      setQueueHealth(queueHealthValue);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [auth, projectId]);

  React.useEffect(() => { void load(); }, [load]);

  const cards = summary ? [
    ["Events", summary.counts.events],
    ["Endpoints", summary.counts.webhook_endpoints],
    ["Replays", summary.counts.replays],
    ["Workflows", summary.counts.workflows],
    ["Workflow runs", summary.counts.workflow_runs],
    ["Pending jobs", summary.counts.pending_jobs],
  ] : [];

  return (
    <>
      <PageHeader eyebrow="CONTROL PLANE" title="Overview" description="Live operational state across the three EventForge products." actions={<button className="button" onClick={() => void load()}>Refresh</button>} />
      <ErrorBanner message={error} />
      {loading && !summary ? <div className="loading-bar">Loading project state…</div> : null}
      <div className="metric-grid">
        {cards.map(([label, value]) => (
          <article className="metric-card" key={String(label)}><span>{label}</span><strong>{value}</strong></article>
        ))}
      </div>
      {summary?.counts.dead_lettered_jobs ? (
        <div className="banner banner-error">{summary.counts.dead_lettered_jobs} job(s) are dead-lettered and require inspection.</div>
      ) : null}
      {queueHealth?.expired_leases ? (
        <div className="banner banner-error">{queueHealth.expired_leases} running job lease(s) are expired and await recovery.</div>
      ) : null}

      <section className="panel reliability-strip">
        <div className="panel-title"><div><span className="kicker">RELIABILITY</span><h3>Queue recovery state</h3></div><span>{queueHealth?.total_recoveries ?? 0} recoveries</span></div>
        <div className="reliability-grid">
          <div><span>Running</span><strong>{queueHealth?.running ?? 0}</strong></div>
          <div><span>Retry wait</span><strong>{queueHealth?.retry_wait ?? 0}</strong></div>
          <div><span>Recovered jobs</span><strong>{queueHealth?.recovered_jobs ?? 0}</strong></div>
          <div><span>Oldest actionable</span><strong>{queueHealth?.oldest_actionable_age_seconds == null ? "—" : `${Math.round(queueHealth.oldest_actionable_age_seconds)}s`}</strong></div>
        </div>
      </section>

      <div className="dashboard-grid">
        <section className="panel span-2">
          <div className="panel-title"><div><span className="kicker">HOOKLEDGER</span><h3>Recent events</h3></div><span>{events.length}</span></div>
          {events.length === 0 ? <EmptyState>No events yet.</EmptyState> : (
            <div className="table-wrap"><table><thead><tr><th>Type</th><th>Source</th><th>Delivery</th><th>Received</th></tr></thead><tbody>
              {events.map((event) => <tr key={event.id}><td><strong>{event.type}</strong><div className="mono-sub">{shortId(event.id)}</div></td><td>{event.source}</td><td>{event.provider_delivery_id ?? "—"}</td><td>{formatTime(event.received_at)}</td></tr>)}
            </tbody></table></div>
          )}
        </section>

        <section className="panel">
          <div className="panel-title"><div><span className="kicker">QUEUE</span><h3>Recent jobs</h3></div><span>{jobs.length}</span></div>
          <div className="list-stack">
            {jobs.slice(0, 6).map((job) => <div className="list-row" key={job.id}><div><strong>{job.kind}</strong><span>{shortId(job.id)} · attempt {job.attempt_count}/{job.max_attempts}</span></div><StatusPill value={job.status} /></div>)}
            {jobs.length === 0 ? <EmptyState>No jobs yet.</EmptyState> : null}
          </div>
        </section>

        <section className="panel">
          <div className="panel-title"><div><span className="kicker">REPLAYDB</span><h3>Recent replays</h3></div><span>{replays.length}</span></div>
          <div className="list-stack">
            {replays.slice(0, 6).map((replay) => <div className="list-row" key={replay.id}><div><strong>{replay.workflow_version_mode}</strong><span>{shortId(replay.replay_of)} · {replay.workflow_selection_count} workflow(s)</span></div><StatusPill value={replay.job_status} /></div>)}
            {replays.length === 0 ? <EmptyState>No replays yet.</EmptyState> : null}
          </div>
        </section>

        <section className="panel span-2">
          <div className="panel-title"><div><span className="kicker">FLOWTRACE</span><h3>Workflows and runs</h3></div><span>{workflows.length} workflows</span></div>
          <div className="split-summary">
            <div className="list-stack">
              {workflows.slice(0, 5).map((workflow) => <div className="list-row" key={workflow.id}><div><strong>{workflow.name}</strong><span>{workflow.trigger_source}/{workflow.trigger_type} · v{workflow.active_version_number ?? "—"}</span></div><StatusPill value={workflow.enabled ? "enabled" : "disabled"} /></div>)}
              {workflows.length === 0 ? <EmptyState>No workflows yet.</EmptyState> : null}
            </div>
            <div className="list-stack">
              {runs.slice(0, 5).map((run) => <div className="list-row" key={run.id}><div><strong>v{run.version_number}</strong><span>{shortId(run.id)} · {formatTime(run.created_at)}</span></div><StatusPill value={run.status} /></div>)}
              {runs.length === 0 ? <EmptyState>No workflow runs yet.</EmptyState> : null}
            </div>
          </div>
        </section>
      </div>
    </>
  );
}

function HookLedger({ auth, projectId, onSecret }: { auth: AuthState; projectId: string; onSecret: (notice: SecretNotice) => void }) {
  const [endpoints, setEndpoints] = React.useState<Endpoint[]>([]);
  const [events, setEvents] = React.useState<EventRow[]>([]);
  const [attempts, setAttempts] = React.useState<IngressAttempt[]>([]);
  const [integration, setIntegration] = React.useState<Record<string, unknown> | null>(null);
  const [selectedEndpoint, setSelectedEndpoint] = React.useState<Endpoint | null>(null);
  const [selectedEvent, setSelectedEvent] = React.useState<EventRow | null>(null);
  const [createOpen, setCreateOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [source, setSource] = React.useState("generic");
  const [verifySignature, setVerifySignature] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      const [endpointRows, eventRows] = await Promise.all([
        apiRequest<Endpoint[]>(auth, `/projects/${projectId}/webhook-endpoints`),
        apiRequest<EventRow[]>(auth, `/projects/${projectId}/events?limit=100`),
      ]);
      setEndpoints(endpointRows);
      setEvents(eventRows);
    } catch (err) {
      setError(errorText(err));
    }
  }, [auth, projectId]);

  React.useEffect(() => { void load(); }, [load]);

  async function createEndpoint(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await postJson<Endpoint & { endpoint_token: string; signing_secret?: string; token_note?: string }>(auth, `/projects/${projectId}/webhook-endpoints`, {
        name,
        source,
        verify_signature: verifySignature,
        signature_tolerance_seconds: 300,
      });
      const rows = [{ label: "Endpoint token", value: created.endpoint_token }];
      if (created.signing_secret) rows.push({ label: "Signing secret", value: created.signing_secret });
      onSecret({ title: `Webhook endpoint: ${created.name}`, rows, note: "Configure the sender with these values now. They are not stored in plaintext." });
      setName("");
      setCreateOpen(false);
      await load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function inspectEndpoint(endpoint: Endpoint) {
    setSelectedEndpoint(endpoint);
    setSelectedEvent(null);
    setIntegration(null);
    try {
      setAttempts(await apiRequest<IngressAttempt[]>(auth, `/webhook-endpoints/${endpoint.id}/ingress-attempts?limit=100`));
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function rotate(endpoint: Endpoint) {
    if (!window.confirm(`Rotate the signing secret for ${endpoint.name}? Existing signatures will stop validating.`)) return;
    try {
      const result = await postJson<{ signing_secret: string; signing_secret_version: number }>(auth, `/webhook-endpoints/${endpoint.id}/rotate-signing-secret`);
      onSecret({ title: `Rotated ${endpoint.name}`, rows: [{ label: `Signing secret v${result.signing_secret_version}`, value: result.signing_secret }], note: "Replace the provider secret immediately. The previous secret no longer validates." });
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function inspectEvent(event: EventRow) {
    setSelectedEvent(event);
    setSelectedEndpoint(null);
    setAttempts([]);
    try {
      setIntegration(await apiRequest<Record<string, unknown>>(auth, `/events/${event.id}/integration`));
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function replay(mode: "original" | "current") {
    if (!selectedEvent) return;
    try {
      await postJson(auth, `/events/${selectedEvent.id}/replays`, { workflow_version_mode: mode });
      setIntegration(await apiRequest<Record<string, unknown>>(auth, `/events/${selectedEvent.id}/integration`));
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function scheduleWorkflows() {
    if (!selectedEvent) return;
    try {
      await postJson(auth, `/events/${selectedEvent.id}/workflow-runs`);
      setIntegration(await apiRequest<Record<string, unknown>>(auth, `/events/${selectedEvent.id}/integration`));
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <>
      <PageHeader eyebrow="HOOKLEDGER" title="Ingress and immutable events" description="Manage webhook endpoints, inspect ingestion attempts, and trace each persisted event across EventForge." actions={<><button className="button" onClick={() => void load()}>Refresh</button><button className="button button-primary" onClick={() => setCreateOpen((v) => !v)}>New endpoint</button></>} />
      <ErrorBanner message={error} />
      {createOpen ? (
        <section className="panel form-panel">
          <h3>Create webhook endpoint</h3>
          <form className="inline-form" onSubmit={createEndpoint}>
            <label><span>Name</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="github-main" required /></label>
            <label><span>Source</span><input value={source} onChange={(event) => setSource(event.target.value)} placeholder="github" required /></label>
            <label className="check-label"><input type="checkbox" checked={verifySignature} onChange={(event) => setVerifySignature(event.target.checked)} /><span>Require HMAC signature</span></label>
            <button className="button button-primary" disabled={busy} type="submit">{busy ? "Creating…" : "Create endpoint"}</button>
          </form>
        </section>
      ) : null}

      <div className="dashboard-grid">
        <section className="panel">
          <div className="panel-title"><div><span className="kicker">ENDPOINTS</span><h3>Webhook endpoints</h3></div><span>{endpoints.length}</span></div>
          <div className="list-stack">
            {endpoints.map((endpoint) => (
              <button className={`list-row row-button ${selectedEndpoint?.id === endpoint.id ? "selected" : ""}`} key={endpoint.id} onClick={() => void inspectEndpoint(endpoint)}>
                <div><strong>{endpoint.name}</strong><span>{endpoint.source} · {endpoint.token_prefix}… · {endpoint.verify_signature ? "signed" : "unsigned"}</span></div>
                <StatusPill value={endpoint.enabled ? "enabled" : "disabled"} />
              </button>
            ))}
            {endpoints.length === 0 ? <EmptyState>No endpoints configured.</EmptyState> : null}
          </div>
        </section>

        <section className="panel span-2">
          <div className="panel-title"><div><span className="kicker">EVENTS</span><h3>Event ledger</h3></div><span>{events.length}</span></div>
          {events.length === 0 ? <EmptyState>No events received.</EmptyState> : (
            <div className="table-wrap"><table><thead><tr><th>Event</th><th>Source</th><th>Delivery</th><th>Bytes</th><th>Received</th></tr></thead><tbody>
              {events.map((event) => <tr className={selectedEvent?.id === event.id ? "selected-row" : ""} key={event.id} onClick={() => void inspectEvent(event)}><td><strong>{event.type}</strong><div className="mono-sub">{shortId(event.id)}</div></td><td>{event.source}</td><td>{event.provider_delivery_id ?? "—"}</td><td>{event.size_bytes}</td><td>{formatTime(event.received_at)}</td></tr>)}
            </tbody></table></div>
          )}
        </section>
      </div>

      {selectedEndpoint ? (
        <section className="panel detail-panel">
          <div className="detail-heading"><div><span className="kicker">ENDPOINT DETAIL</span><h3>{selectedEndpoint.name}</h3><p>{selectedEndpoint.id}</p></div><button className="button" onClick={() => void rotate(selectedEndpoint)}>Rotate signing secret</button></div>
          <div className="detail-facts"><span><b>Source</b>{selectedEndpoint.source}</span><span><b>Signature</b>{selectedEndpoint.verify_signature ? `required · v${selectedEndpoint.signing_secret_version}` : "not required"}</span><span><b>Last event</b>{formatTime(selectedEndpoint.last_received_at)}</span></div>
          <div className="panel-title"><h4>Ingress attempts</h4><span>{attempts.length}</span></div>
          <div className="table-wrap"><table><thead><tr><th>Outcome</th><th>HTTP</th><th>Delivery</th><th>Received</th></tr></thead><tbody>{attempts.map((attempt) => <tr key={attempt.id}><td><StatusPill value={attempt.outcome} /></td><td>{attempt.response_code}</td><td>{attempt.provider_delivery_id ?? "—"}</td><td>{formatTime(attempt.received_at)}</td></tr>)}</tbody></table></div>
        </section>
      ) : null}

      {selectedEvent ? (
        <section className="panel detail-panel">
          <div className="detail-heading"><div><span className="kicker">EVENT INTEGRATION</span><h3>{selectedEvent.type}</h3><p>{selectedEvent.id}</p></div><div className="button-row"><button className="button" onClick={() => void scheduleWorkflows()}>Run current workflows</button><button className="button" onClick={() => void replay("original")}>Replay original</button><button className="button button-primary" onClick={() => void replay("current")}>Replay current</button></div></div>
          {integration ? <JsonBlock value={integration} /> : <div className="loading-bar">Loading integration view…</div>}
        </section>
      ) : null}
    </>
  );
}

function ReplayDB({ auth, projectId }: { auth: AuthState; projectId: string }) {
  const [replays, setReplays] = React.useState<ReplayRow[]>([]);
  const [events, setEvents] = React.useState<EventRow[]>([]);
  const [selected, setSelected] = React.useState<ReplayRow | null>(null);
  const [detail, setDetail] = React.useState<Record<string, unknown> | null>(null);
  const [selections, setSelections] = React.useState<ReplaySelection[]>([]);
  const [eventId, setEventId] = React.useState("");
  const [mode, setMode] = React.useState<"original" | "current">("original");
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      const [replayRows, eventRows] = await Promise.all([
        apiRequest<ReplayRow[]>(auth, `/projects/${projectId}/replays?limit=100`),
        apiRequest<EventRow[]>(auth, `/projects/${projectId}/events?limit=100`),
      ]);
      setReplays(replayRows);
      setEvents(eventRows);
      if (!eventId && eventRows[0]) setEventId(eventRows[0].id);
    } catch (err) {
      setError(errorText(err));
    }
  }, [auth, projectId, eventId]);

  React.useEffect(() => { void load(); }, [auth, projectId]);

  async function createReplay(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const created = await postJson<ReplayRow>(auth, `/events/${eventId}/replays`, { workflow_version_mode: mode });
      await load();
      await inspect(created);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function inspect(row: ReplayRow) {
    setSelected(row);
    setError(null);
    try {
      const [detailValue, selectionValue] = await Promise.all([
        apiRequest<Record<string, unknown>>(auth, `/replays/${row.id}`),
        apiRequest<ReplaySelection[]>(auth, `/replays/${row.id}/workflow-selections`),
      ]);
      setDetail(detailValue);
      setSelections(selectionValue);
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <>
      <PageHeader eyebrow="REPLAYDB" title="Deterministic replay" description="Re-execute immutable historical events against their original or current FlowTrace workflow selection." actions={<button className="button" onClick={() => void load()}>Refresh</button>} />
      <ErrorBanner message={error} />
      <section className="panel form-panel">
        <h3>Create replay</h3>
        <form className="inline-form" onSubmit={createReplay}>
          <label className="grow"><span>Source event</span><select value={eventId} onChange={(event) => setEventId(event.target.value)} required><option value="">Choose an event</option>{events.map((event) => <option value={event.id} key={event.id}>{event.type} · {event.source} · {shortId(event.id)}</option>)}</select></label>
          <label><span>Workflow mode</span><select value={mode} onChange={(event) => setMode(event.target.value as "original" | "current")}><option value="original">original</option><option value="current">current</option></select></label>
          <button className="button button-primary" type="submit" disabled={!eventId}>Create replay</button>
        </form>
      </section>

      <section className="panel">
        <div className="panel-title"><div><span className="kicker">HISTORY</span><h3>Replay executions</h3></div><span>{replays.length}</span></div>
        {replays.length === 0 ? <EmptyState>No replay executions yet.</EmptyState> : <div className="table-wrap"><table><thead><tr><th>Replay</th><th>Source event</th><th>Mode</th><th>Selection</th><th>Status</th><th>Created</th></tr></thead><tbody>{replays.map((replay) => <tr className={selected?.id === replay.id ? "selected-row" : ""} key={replay.id} onClick={() => void inspect(replay)}><td className="mono-sub">{shortId(replay.id)}</td><td className="mono-sub">{shortId(replay.replay_of)}</td><td>{replay.workflow_version_mode}</td><td>{replay.workflow_selection_count}</td><td><StatusPill value={replay.job_status} /></td><td>{formatTime(replay.created_at)}</td></tr>)}</tbody></table></div>}
      </section>

      {selected ? (
        <section className="panel detail-panel">
          <div className="detail-heading"><div><span className="kicker">REPLAY DETAIL</span><h3>{shortId(selected.id, 16)}</h3><p>{selected.id}</p></div><StatusPill value={selected.job_status} /></div>
          <div className="split-summary">
            <div><h4>Frozen workflow selection</h4>{selections.length === 0 ? <EmptyState>No workflows were selected for this replay.</EmptyState> : <div className="list-stack">{selections.map((selection) => <div className="list-row" key={selection.workflow_version_id}><div><strong>{selection.workflow_name}</strong><span>{shortId(selection.workflow_id)} · version {selection.version_number}</span></div><StatusPill value={`v${selection.version_number}`} /></div>)}</div>}</div>
            <div><h4>Execution record</h4>{detail ? <JsonBlock value={detail} /> : <div className="loading-bar">Loading…</div>}</div>
          </div>
        </section>
      ) : null}
    </>
  );
}

function FlowTrace({ auth, projectId }: { auth: AuthState; projectId: string }) {
  const [workflows, setWorkflows] = React.useState<WorkflowRow[]>([]);
  const [runs, setRuns] = React.useState<WorkflowRun[]>([]);
  const [selectedWorkflow, setSelectedWorkflow] = React.useState<WorkflowRow | null>(null);
  const [versions, setVersions] = React.useState<WorkflowVersion[]>([]);
  const [selectedRun, setSelectedRun] = React.useState<WorkflowRun | null>(null);
  const [runDetail, setRunDetail] = React.useState<Record<string, unknown> | null>(null);
  const [steps, setSteps] = React.useState<StepRun[]>([]);
  const [createOpen, setCreateOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [triggerSource, setTriggerSource] = React.useState("generic");
  const [triggerType, setTriggerType] = React.useState("event.created");
  const [stepsJson, setStepsJson] = React.useState('[\n  {"type":"json_transform","set":{"flowtrace.status":"started"}}\n]');
  const [newVersionJson, setNewVersionJson] = React.useState('[\n  {"type":"json_transform","set":{"flowtrace.version":"next"}}\n]');
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      const [workflowRows, runRows] = await Promise.all([
        apiRequest<WorkflowRow[]>(auth, `/projects/${projectId}/workflows`),
        apiRequest<WorkflowRun[]>(auth, `/projects/${projectId}/workflow-runs?limit=100`),
      ]);
      setWorkflows(workflowRows);
      setRuns(runRows);
    } catch (err) {
      setError(errorText(err));
    }
  }, [auth, projectId]);

  React.useEffect(() => { void load(); }, [load]);

  async function createWorkflow(event: React.FormEvent) {
    event.preventDefault();
    try {
      const parsed = JSON.parse(stepsJson) as unknown;
      if (!Array.isArray(parsed)) throw new Error("Steps JSON must be an array");
      await postJson(auth, `/projects/${projectId}/workflows`, { name, trigger: { source: triggerSource, type: triggerType }, steps: parsed });
      setName("");
      setCreateOpen(false);
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function inspectWorkflow(workflow: WorkflowRow) {
    setSelectedWorkflow(workflow);
    setSelectedRun(null);
    try {
      setVersions(await apiRequest<WorkflowVersion[]>(auth, `/workflows/${workflow.id}/versions`));
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function activate(version: WorkflowVersion) {
    if (!selectedWorkflow) return;
    try {
      await postJson(auth, `/workflows/${selectedWorkflow.id}/versions/${version.version_number}/activate`);
      await inspectWorkflow(selectedWorkflow);
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function addVersion(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedWorkflow) return;
    try {
      const parsed = JSON.parse(newVersionJson) as unknown;
      if (!Array.isArray(parsed)) throw new Error("Steps JSON must be an array");
      await postJson(auth, `/workflows/${selectedWorkflow.id}/versions`, {
        trigger: { source: selectedWorkflow.trigger_source ?? "generic", type: selectedWorkflow.trigger_type ?? "event.created" },
        steps: parsed,
        activate: true,
      });
      await inspectWorkflow(selectedWorkflow);
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function inspectRun(run: WorkflowRun) {
    setSelectedRun(run);
    setSelectedWorkflow(null);
    try {
      const [detailValue, stepValue] = await Promise.all([
        apiRequest<Record<string, unknown>>(auth, `/workflow-runs/${run.id}`),
        apiRequest<StepRun[]>(auth, `/workflow-runs/${run.id}/steps`),
      ]);
      setRunDetail(detailValue);
      setSteps(stepValue);
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <>
      <PageHeader eyebrow="FLOWTRACE" title="Durable workflows" description="Inspect versioned workflows, activate definitions, and follow persisted step execution." actions={<><button className="button" onClick={() => void load()}>Refresh</button><button className="button button-primary" onClick={() => setCreateOpen((value) => !value)}>New workflow</button></>} />
      <ErrorBanner message={error} />
      {createOpen ? (
        <section className="panel form-panel">
          <h3>Create workflow</h3>
          <form className="workflow-form" onSubmit={createWorkflow}>
            <div className="inline-form"><label className="grow"><span>Name</span><input value={name} onChange={(event) => setName(event.target.value)} required /></label><label><span>Trigger source</span><input value={triggerSource} onChange={(event) => setTriggerSource(event.target.value)} required /></label><label><span>Trigger type</span><input value={triggerType} onChange={(event) => setTriggerType(event.target.value)} required /></label></div>
            <label><span>Steps JSON</span><textarea rows={8} value={stepsJson} onChange={(event) => setStepsJson(event.target.value)} spellCheck={false} /></label>
            <button className="button button-primary" type="submit">Create and activate v1</button>
          </form>
        </section>
      ) : null}

      <div className="dashboard-grid">
        <section className="panel">
          <div className="panel-title"><div><span className="kicker">WORKFLOWS</span><h3>Definitions</h3></div><span>{workflows.length}</span></div>
          <div className="list-stack">{workflows.map((workflow) => <button className={`list-row row-button ${selectedWorkflow?.id === workflow.id ? "selected" : ""}`} key={workflow.id} onClick={() => void inspectWorkflow(workflow)}><div><strong>{workflow.name}</strong><span>{workflow.trigger_source}/{workflow.trigger_type} · active v{workflow.active_version_number ?? "—"}</span></div><StatusPill value={workflow.enabled ? "enabled" : "disabled"} /></button>)}{workflows.length === 0 ? <EmptyState>No workflows configured.</EmptyState> : null}</div>
        </section>

        <section className="panel span-2">
          <div className="panel-title"><div><span className="kicker">RUNS</span><h3>Execution history</h3></div><span>{runs.length}</span></div>
          {runs.length === 0 ? <EmptyState>No workflow runs yet.</EmptyState> : <div className="table-wrap"><table><thead><tr><th>Run</th><th>Workflow</th><th>Version</th><th>Status</th><th>Replay</th><th>Created</th></tr></thead><tbody>{runs.map((run) => <tr className={selectedRun?.id === run.id ? "selected-row" : ""} key={run.id} onClick={() => void inspectRun(run)}><td className="mono-sub">{shortId(run.id)}</td><td className="mono-sub">{shortId(run.workflow_id)}</td><td>v{run.version_number}</td><td><StatusPill value={run.status} /></td><td>{run.replay_execution_id ? shortId(run.replay_execution_id) : "—"}</td><td>{formatTime(run.created_at)}</td></tr>)}</tbody></table></div>}
        </section>
      </div>

      {selectedWorkflow ? (
        <section className="panel detail-panel">
          <div className="detail-heading"><div><span className="kicker">WORKFLOW VERSIONS</span><h3>{selectedWorkflow.name}</h3><p>{selectedWorkflow.id}</p></div></div>
          <div className="version-grid">{versions.map((version) => <article className={`version-card ${version.active ? "active" : ""}`} key={version.id}><div className="version-head"><strong>v{version.version_number}</strong>{version.active ? <StatusPill value="active" /> : <button className="button button-small" onClick={() => void activate(version)}>Activate</button>}</div><span>{version.trigger_source}/{version.trigger_type}</span><JsonBlock value={version.definition} /></article>)}</div>
          <form className="workflow-form new-version" onSubmit={addVersion}><h4>Create next version</h4><label><span>Steps JSON</span><textarea rows={7} value={newVersionJson} onChange={(event) => setNewVersionJson(event.target.value)} spellCheck={false} /></label><button className="button button-primary" type="submit">Create and activate</button></form>
        </section>
      ) : null}

      {selectedRun ? (
        <section className="panel detail-panel">
          <div className="detail-heading"><div><span className="kicker">RUN DETAIL</span><h3>{shortId(selectedRun.id, 16)}</h3><p>{selectedRun.id}</p></div><StatusPill value={selectedRun.status} /></div>
          <div className="step-timeline">{steps.map((step) => <article className="step-card" key={step.id}><div className="step-index">{step.step_index}</div><div className="step-body"><div className="step-title"><strong>{step.step_type}</strong><StatusPill value={step.status} /></div><span>{formatTime(step.started_at)} → {formatTime(step.completed_at)}</span>{step.error ? <div className="banner banner-error">{step.error}</div> : null}<JsonBlock value={{ config: step.config, output: step.output }} /></div></article>)}</div>
          {runDetail ? <details><summary>Run record JSON</summary><JsonBlock value={runDetail} /></details> : null}
        </section>
      ) : null}
    </>
  );
}

function SecurityView({ auth, projectId, onSecret }: { auth: AuthState; projectId: string; onSecret: (notice: SecretNotice) => void }) {
  const [keys, setKeys] = React.useState<ApiKeyRow[]>([]);
  const [name, setName] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    if (auth.mode !== "admin") return;
    try {
      setKeys(await apiRequest<ApiKeyRow[]>(auth, `/projects/${projectId}/api-keys`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [auth, projectId]);

  React.useEffect(() => { void load(); }, [load]);

  if (auth.mode !== "admin") {
    return <><PageHeader eyebrow="SECURITY" title="Project credentials" description="API-key lifecycle is intentionally restricted to the administrator bootstrap identity." /><div className="banner">Reconnect as an administrator to create, list, or revoke project API keys.</div></>;
  }

  async function createKey(event: React.FormEvent) {
    event.preventDefault();
    try {
      const created = await postJson<ApiKeyRow & { api_key: string }>(auth, `/projects/${projectId}/api-keys`, { name });
      onSecret({ title: `API key: ${created.name}`, rows: [{ label: "Project API key", value: created.api_key }], note: "This key is shown once. EventForge stores only its SHA-256 hash and prefix." });
      setName("");
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function revoke(key: ApiKeyRow) {
    if (!window.confirm(`Revoke ${key.name}? Existing clients using this key will immediately receive 401.`)) return;
    try {
      await postJson(auth, `/project-api-keys/${key.id}/revoke`);
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <>
      <PageHeader eyebrow="SECURITY" title="Project API keys" description="Create one-time project credentials, inspect usage, and revoke access without exposing stored secrets." actions={<button className="button" onClick={() => void load()}>Refresh</button>} />
      <ErrorBanner message={error} />
      <section className="panel form-panel"><h3>Create API key</h3><form className="inline-form" onSubmit={createKey}><label className="grow"><span>Name</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="ci-production" required /></label><button className="button button-primary" type="submit">Create key</button></form></section>
      <section className="panel"><div className="panel-title"><div><span className="kicker">CREDENTIALS</span><h3>Keys</h3></div><span>{keys.length}</span></div>{keys.length === 0 ? <EmptyState>No API keys for this project.</EmptyState> : <div className="table-wrap"><table><thead><tr><th>Name</th><th>Prefix</th><th>Created</th><th>Last used</th><th>Status</th><th></th></tr></thead><tbody>{keys.map((key) => <tr key={key.id}><td><strong>{key.name}</strong><div className="mono-sub">{shortId(key.id)}</div></td><td><code>{key.key_prefix}…</code></td><td>{formatTime(key.created_at)}</td><td>{formatTime(key.last_used_at)}</td><td><StatusPill value={key.revoked_at ? "revoked" : "active"} /></td><td>{key.revoked_at ? null : <button className="button button-danger button-small" onClick={() => void revoke(key)}>Revoke</button>}</td></tr>)}</tbody></table></div>}</section>
    </>
  );
}

export default function App() {
  const [root, setRoot] = React.useState<RootInfo | null>(null);
  const [auth, setAuth] = React.useState<AuthState | null>(null);
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [projectId, setProjectId] = React.useState("");
  const [view, setView] = React.useState<ViewName>("overview");
  const [secret, setSecret] = React.useState<SecretNotice | null>(null);
  const [projectName, setProjectName] = React.useState("");
  const [showCreateProject, setShowCreateProject] = React.useState(false);
  const [globalError, setGlobalError] = React.useState<string | null>(null);

  React.useEffect(() => {
    publicGet<RootInfo>("/").then(setRoot).catch(() => setRoot(null));
  }, []);

  async function login(nextAuth: AuthState) {
    setGlobalError(null);
    if (nextAuth.mode === "admin") {
      const rows = await apiRequest<Project[]>(nextAuth, "/projects");
      setProjects(rows);
      setProjectId(rows[0]?.id ?? "");
    } else {
      await apiRequest<ProjectSummary>(nextAuth, `/projects/${nextAuth.projectId}/summary`);
      setProjects([]);
      setProjectId(nextAuth.projectId);
    }
    setAuth(nextAuth);
    setView("overview");
  }

  function logout() {
    setAuth(null);
    setProjects([]);
    setProjectId("");
    setSecret(null);
  }

  async function createProject(event: React.FormEvent) {
    event.preventDefault();
    if (!auth || auth.mode !== "admin") return;
    try {
      const project = await postJson<Project>(auth, "/projects", { name: projectName });
      const rows = await apiRequest<Project[]>(auth, "/projects");
      setProjects(rows);
      setProjectId(project.id);
      setProjectName("");
      setShowCreateProject(false);
    } catch (err) {
      setGlobalError(errorText(err));
    }
  }

  if (!auth) return <LoginScreen root={root} onLogin={login} />;

  const currentProject = projects.find((project) => project.id === projectId);
  const nav: Array<[ViewName, string, string]> = [
    ["overview", "Overview", "01"],
    ["hookledger", "HookLedger", "02"],
    ["replaydb", "ReplayDB", "03"],
    ["flowtrace", "FlowTrace", "04"],
    ["security", "Security", "05"],
  ];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand"><div className="brand-mark small">EF</div><div><strong>EventForge</strong><span>{root?.version ?? "v0.8.0"}</span></div></div>
        <nav>{nav.map(([key, label, number]) => <button key={key} className={view === key ? "active" : ""} onClick={() => setView(key)}><span>{number}</span>{label}</button>)}</nav>
        <div className="sidebar-foot"><span>{auth.mode === "admin" ? "Administrator" : "Project key"}</span><button className="button button-ghost button-wide" onClick={logout}>Disconnect</button></div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="project-switcher">
            <span>Project</span>
            {auth.mode === "admin" ? (
              <select value={projectId} onChange={(event) => setProjectId(event.target.value)}>
                <option value="">Choose a project</option>
                {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
              </select>
            ) : <strong>{shortId(projectId, 18)}</strong>}
            {auth.mode === "admin" ? <button className="button button-small" onClick={() => setShowCreateProject((value) => !value)}>New project</button> : null}
          </div>
          <div className="topbar-status"><span className="dot" />{root ? `${root.status} · ${root.version}` : "API status unavailable"}</div>
        </header>

        <main className="content">
          <ErrorBanner message={globalError} />
          {showCreateProject ? <section className="panel create-project"><form className="inline-form" onSubmit={createProject}><label className="grow"><span>Project name</span><input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="payments-platform" required /></label><button className="button button-primary" type="submit">Create</button></form></section> : null}
          {secret ? <SecretPanel notice={secret} onClose={() => setSecret(null)} /> : null}

          {!projectId ? (
            <section className="welcome-panel"><span className="kicker">NO PROJECT SELECTED</span><h2>Create or choose a project to enter the EventForge control plane.</h2><p>The dashboard deliberately keeps all product data scoped to one project at a time.</p></section>
          ) : (
            <>
              {currentProject ? <div className="project-context"><strong>{currentProject.name}</strong><code>{currentProject.id}</code></div> : null}
              {view === "overview" ? <Overview auth={auth} projectId={projectId} /> : null}
              {view === "hookledger" ? <HookLedger auth={auth} projectId={projectId} onSecret={setSecret} /> : null}
              {view === "replaydb" ? <ReplayDB auth={auth} projectId={projectId} /> : null}
              {view === "flowtrace" ? <FlowTrace auth={auth} projectId={projectId} /> : null}
              {view === "security" ? <SecurityView auth={auth} projectId={projectId} onSecret={setSecret} /> : null}
            </>
          )}
        </main>
      </div>
    </div>
  );
}
