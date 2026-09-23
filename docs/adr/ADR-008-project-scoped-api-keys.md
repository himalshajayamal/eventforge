# ADR-008: Project-scoped API keys

## Status

Accepted for EventForge v0.6.0.

## Context

The pre-v0.6 control plane used one development administrator Basic identity for
all projects. That was sufficient for local engineering but did not create a
project authorization boundary.

## Decision

Keep Basic authentication only as the bootstrap/administrator identity and add
high-entropy project API keys for normal automation access.

Each API key stores:

```text
id
project_id
name
key_prefix
SHA-256(key)
created_at
last_used_at
revoked_at
```

The plaintext key is returned once. Project-key requests are authorized against
the `project_id` of the URL or addressed object. Cross-project requests return
404 instead of disclosing object existence.

## Consequences

- Automation no longer needs the global administrator credential.
- Keys can be revoked independently.
- The admin Basic identity remains a pre-v1 bootstrap mechanism rather than a
  complete human identity/RBAC system.
- High-entropy generated API keys make a fast SHA-256 lookup appropriate; this
  decision does not apply to human passwords.
