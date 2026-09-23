# Upgrade to EventForge v0.6.0

## 1. Apply migration 0007 once

```bat
docker compose exec -T db psql -U eventforge -d eventforge -v ON_ERROR_STOP=1 < database\migrations\0007_security.sql
```

Verify:

```bat
docker compose exec db psql -U eventforge -d eventforge -c "SELECT * FROM schema_migrations ORDER BY version;"
```

The final row should be `0007_security`.

## 2. Review security environment values

Development defaults remain usable locally. Before production set non-default
values for at least:

```text
EVENTFORGE_ENV=production
EVENTFORGE_ADMIN_PASSWORD=<strong-secret>
EVENTFORGE_WEBHOOK_MASTER_SECRET=<32+-character-secret>
```

`EVENTFORGE_ENV=production` refuses the known development secrets.

Optional outbound controls:

```text
EVENTFORGE_HTTP_ACTION_ALLOWED_PORTS=443
EVENTFORGE_ALLOW_PRIVATE_HTTP_ACTIONS=0
EVENTFORGE_ALLOW_INSECURE_HTTP_ACTIONS=0
```

Do not enable the private/insecure overrides on a public deployment unless the
network model explicitly requires them.

## 3. Rebuild

```bat
docker compose up -d --build
```

Expected root response:

```json
{"name":"EventForge","version":"0.6.0","status":"security-hardened"}
```

## 4. Run the isolated suite

```bat
docker compose run --rm test
```

Expected v0.6 suite: 30 tests.

## 5. Preferred webhook ingestion

Use the header-token route for new integrations:

```text
POST /ingest
X-EventForge-Endpoint-Token: <token>
```

The path-token route remains compatible during v0.x.

## 6. Signed endpoint setup

Create an endpoint with `verify_signature=true`. EventForge returns the signing
secret during setup. Configure the provider with that secret. EventForge does
not store the plaintext signing secret.

For EventForge-format signatures, compute HMAC-SHA256 over:

```text
<unix-timestamp>.<raw-body>
```

and send `X-EventForge-Timestamp` plus `X-EventForge-Signature`.

## 7. API keys

Administrator credentials issue project keys. Store the returned `efk_...` key
because it is not retrievable later. Send it as:

```text
Authorization: Bearer efk_...
```

Project keys receive only their own project scope.

## Compatibility notes

- Existing webhook endpoints remain unsigned until explicitly rotated/enabled.
- New endpoints in production mode require signatures.
- Existing Basic-auth administrator scripts continue to work.
- Existing HookLedger, ReplayDB and FlowTrace database records remain valid.
- The legacy secret-bearing ingest URL remains available but normal Uvicorn
  access logging is disabled.
