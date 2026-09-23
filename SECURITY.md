# EventForge security notes

EventForge is still pre-1.0 and local-first. Do not expose the development
configuration directly to the public internet.

For production-like testing, use `EVENTFORGE_ENV=production`, replace the
default administrator password and webhook master secret, use signed webhook
endpoints, issue project-scoped API keys to automation, and leave private HTTP
actions disabled.

Secrets that appear in terminal transcripts, screenshots, issue trackers, or
chat logs should be treated as exposed and rotated before nonlocal use.

Report security issues privately to the repository owner rather than opening a
public proof-of-concept issue containing live credentials or endpoint tokens.
