# Changelog

## 0.2.0 — 2026-10-27

First public release, including the structured write implementation developed against
the Beacon G6 web UI.

- Create, replace, and delete local IPv4 DNS overrides with previews, idempotence,
  read-back verification, and conservative recovery after definite replacement failure.
- Add and delete static TCP, UDP, and combined-protocol forwards, including ranges,
  WAN selection, conflict detection, and full-identity deletion.
- Export configuration backups to private local files; return metadata only.
- Avoid replaying configuration POSTs after ambiguous failures or redirects.
- Serialize operations per server process and require a separate opt-in for raw writes.
- Add offline tests and macOS/Linux/Windows CI, including installed-wheel MCP discovery.
- Refresh setup, Hermes configuration, tool reference, and sanitized protocol notes;
  add a README illustration.

Public history starts with a sanitized source snapshot. Credentials and operator-specific
network notes are not included. If upgrading from an earlier private checkout, clone
the public repository into a new directory and transfer your private `.env` locally;
do not merge the old private history into the public repository.
