# EventForge v0.7.0 — Unified Operations Dashboard

EventForge v1.0 remains limited to the same three user-facing products:

```text
HookLedger  -> receive and persist events
ReplayDB    -> replay immutable historical events
FlowTrace   -> execute durable structured workflows
```

v0.7.0 adds the first complete browser control surface over that integrated core.
It does **not** add a fourth product.

## Unified frontend

The React/Vite application at `http://localhost:5173` now provides one project-scoped dashboard with:

```text
Overview
HookLedger
  webhook endpoints
  event ledger
  ingress attempts
  cross-product event integration view
ReplayDB
  replay creation
  replay history
  frozen workflow-version selections
FlowTrace
  workflows
  workflow versions
  activation
  workflow runs
  persisted step runs
Security
  project API-key creation
  last-used/revocation status
  revocation
```

The frontend contains no product-specific backend of its own. It calls the same
FastAPI control plane used by automation clients.

## Dashboard authentication

The dashboard supports both v0.6 authentication modes:

- administrator Basic authentication, which can list/create projects and manage project API keys;
- project-scoped `efk_...` API keys, which require the project UUID and remain restricted to that project.

Credentials are held only in React memory for the current page lifetime. The
frontend does not write administrator passwords or API keys to localStorage or
sessionStorage.

One-time API keys, webhook endpoint tokens, and webhook signing secrets are
shown in a dedicated one-time secret panel so they can be copied before the
plaintext value is discarded.

## Dashboard read projections

v0.7 adds two small read-only control-plane endpoints required by the UI:

```text
GET /projects
GET /projects/{project_id}/summary
```

`GET /projects` is administrator-only. A project API key cannot enumerate the
global project collection.

The project summary endpoint is project-scoped and returns counts for endpoints,
events, jobs, pending jobs, dead-lettered jobs, replays, workflows, and workflow
runs without requiring the browser to download every historical row.

## API identity

```json
{"name":"EventForge","version":"0.7.0","status":"unified-dashboard"}
```

## Local validation

The canonical backend test command remains:

```bat
docker compose run --rm --build test
```

v0.7 contains 32 API tests, including the new dashboard project-list and project-summary boundaries.

The canonical frontend production build is:

```bat
docker compose exec web npm run build
```

## Milestone boundary

v0.7 is a pre-v1 engineering console rather than a finished multi-user SaaS UI.
It intentionally does not add OAuth, organizations, billing, RBAC, arbitrary
workflow code, or a fourth EventForge product.

The next milestone is v0.8 failure/load/recovery hardening across the existing
HookLedger + ReplayDB + FlowTrace system.
