# EventForge v0.6.0 — Security-Hardened Core

EventForge v1.0 remains limited to the same three user-facing products:

```text
HookLedger  -> receive and persist events
ReplayDB    -> replay immutable historical events
FlowTrace   -> execute durable structured workflows
```

v0.6.0 adds security and authorization boundaries around that integrated core.
It does **not** add another product.

## Control-plane authentication

The development administrator Basic credential remains as the bootstrap/admin
identity. v0.6 adds project-scoped API keys for normal automation access.

```text
POST /projects/{project_id}/api-keys
GET  /projects/{project_id}/api-keys
POST /project-api-keys/{api_key_id}/revoke
```

API keys use the `efk_...` format. The plaintext key is returned once; only its
SHA-256 hash and a short prefix are stored. A project API key can access only
objects that resolve to its own `project_id`. Cross-project object requests
return 404 so object existence is not disclosed.

The global administrator remains required to create projects, create/list API
keys, and revoke API keys.

## Signed HookLedger ingestion

Webhook endpoints can now require HMAC verification:

```json
{
  "name": "github-main",
  "source": "github",
  "verify_signature": true,
  "signature_tolerance_seconds": 300
}
```

EventForge derives the endpoint signing secret from a server-side master
secret plus the endpoint id/version. No plaintext endpoint signing secret is
stored in PostgreSQL.

Preferred EventForge signature format:

```text
X-EventForge-Timestamp: <unix-seconds>
X-EventForge-Signature: sha256=<hex-hmac>
```

The signed bytes are:

```text
<timestamp>.<raw-request-body>
```

GitHub's `X-Hub-Signature-256` is also accepted for endpoints whose source is
`github`. GitHub's signature format does not include a timestamp; EventForge's
existing delivery-id idempotency still prevents duplicate logical events, but
it is not a freshness proof.

New production-mode endpoints always require signatures. Development mode keeps
signature verification opt-in so existing local fixtures remain compatible.

Signing secrets can be rotated:

```text
POST /webhook-endpoints/{endpoint_id}/rotate-signing-secret
```

## Secret-bearing ingress path

The preferred ingestion interface is now:

```text
POST /ingest
X-EventForge-Endpoint-Token: <endpoint-token>
```

The old `/ingest/{endpoint_token}` route remains during the v0.x compatibility
window. Uvicorn access logging is disabled so the legacy secret-bearing URL is
not written to normal access logs.

## FlowTrace outbound-request boundary

`http_request` actions now use a stricter outbound policy:

```text
HTTPS required by default
port 443 allowed by default
userinfo rejected
URL fragments rejected
localhost blocked
private / loopback / link-local / multicast / reserved IPs blocked
cloud metadata hostnames blocked
all DNS answers validated
connection pinned to a validated resolved IP
redirects never followed
response bytes capped
request timeout capped
Idempotency-Key = step_run_id
```

The validated IP is used for the actual TCP connection while TLS SNI and the
HTTP Host value remain the original hostname. This removes the previous gap
where validation and the outbound connection could resolve the hostname
independently.

## Production configuration gate

Set:

```text
EVENTFORGE_ENV=production
```

Production startup rejects the known development administrator password and the
known development webhook master secret. The production administrator password
must be at least 16 characters and the webhook master secret at least 32.

New production webhook endpoints require signatures regardless of the optional
development override.

## Security audit visibility

Rejected signed-webhook requests are recorded as:

```text
REJECTED_SIGNATURE
```

without storing the supplied signature. Endpoint attempts can be inspected with:

```text
GET /webhook-endpoints/{endpoint_id}/ingress-attempts
```

## API identity

```json
{"name":"EventForge","version":"0.6.0","status":"security-hardened"}
```

## Local test command

The isolated PostgreSQL test runner introduced in v0.5 remains canonical:

```bat
docker compose run --rm test
```

v0.6 adds security tests for project isolation, API-key revocation, HMAC
freshness, signing-secret rotation, SSRF targets, production configuration, and
security response headers.

## Milestone boundary

v0.6 is not a complete identity platform. There are no user accounts, OAuth,
SSO, organization roles, or fine-grained RBAC yet. The administrator Basic
credential remains a bootstrap mechanism for the local-first pre-v1 system.

The next milestone is the unified frontend for HookLedger + ReplayDB +
FlowTrace, not a fourth product.
