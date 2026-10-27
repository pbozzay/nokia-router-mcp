# Maintainer handoff

Release: 0.2.0. Updated: 2026-10-27.

This document describes the project, not an operator's network. Keep router credentials,
serial numbers, MAC addresses, host inventories, and configuration exports outside Git.

## Current capabilities

The server supports standard Beacon login, status reads, local IPv4 DNS create/replace/
delete, static port-forward add/delete, configuration export, and explicit reboot.
Write tools are opt-in; structured DNS and NAT writes preview by default and verify
applied state. Raw writes require a second opt-in. See [README](README.md) for setup and
[tool reference](docs/tools.md) for the agent workflow.

## Source map

| File | Responsibility |
|---|---|
| `src/nokia_router_mcp/client.py` | Session, login, RSA/AES transport, CSRF, authenticated reads, single-attempt writes |
| `src/nokia_router_mcp/server.py` | MCP tool registry, environment loading, response redaction, process-wide client initialization |
| `src/nokia_router_mcp/operations.py` | DNS lifecycle and local backup export |
| `src/nokia_router_mcp/port_forwarding.py` | WAN/rule normalization, conflict detection, full-identity static rule deletion |
| `src/nokia_router_mcp/write_support.py` | Shared input validation, request construction, write/read-back result handling |
| `src/nokia_router_mcp/cli.py` | stdio server, connection check, diagnostic probe, backup commands |
| `tests/` | Offline stateful router tests, transport failure tests, tool registration and stdio discovery |
| `docs/research/` | Endpoint map, API method map, observed write forms |

## Protocol

The router's Angular web UI calls relative `*_web_app.cgi` endpoints. Standard login
obtains a nonce and RSA public key, encrypts credentials with a temporary AES key, and
wraps that key with RSA. The session uses cookies and a CSRF token. Reads return JSON;
selected WiFi values are AES-encrypted. Writes use encrypted form bodies with CSRF.

Session checks occur after idle periods. Read requests can reauthenticate and retry;
configuration POSTs must never be replayed after ambiguous errors. A reentrant lock
serializes read/validate/write/verify transactions in one MCP process. Separate server
processes and the web UI can still modify the same router concurrently.

Nokia JavaScript bundles are not distributed here. Obtain firmware-specific bundles
from the router's `/web_whw/` page when researching another version. The endpoint map
does not imply every endpoint is safe to call: some GET endpoints initiate actions.

## Verification record

Hardware: Beacon G6, web UI version 2501.20.03.A17, checked 2026-09-28.

- Standard login, stdio discovery, status reads, and DNS previews worked.
- DNS create, replace, repeated no-op, delete, and direct resolution were checked with a
  temporary hostname. Original entries matched the baseline after cleanup.
- A temporary TCP forward was added, read back, repeated as a no-op, and removed.
  Original static forward state matched the baseline afterward.
- Configuration export returned nonempty opaque bytes and a checksum. Restore was not run.
- Some status endpoints are model-dependent: optical status was unavailable on the
  Ethernet-WAN device; a 6 GHz query returned null; one WLAN query timed out.

Offline tests cover validation, conflicts, WAN selection, dynamic-rule preservation,
unequal-range deletion, malformed status, definite rejection/rollback, ambiguous writes,
redirect/timeout behavior, exports, concurrent initialization, and tool gating.
CI runs the suite and installed-wheel discovery on Windows, macOS, and Linux.

Hardware coverage still needed: UDP and combined-protocol ranges, multiple WANs,
rejection/recovery behavior, and connectivity from an external WAN client. FastMile login
is experimental. No reboot, proxy toggle, restore, or firmware update was exercised.

## Next development candidates

1. Add structured DHCP reservations and DNS aliases after verifying the firmware's
   forms and deletion identity. Start with previews and offline fixtures.
2. Expand hardware coverage and collect sanitized fixtures from other models.
3. Consider a coordinated app setup workflow across local DNS, public DNS, and a reverse
   proxy. Cloudflare and Nginx Proxy Manager need separate integrations today.

For each new write: validate fresh state, preview exact intent, send once, verify through
reads, and report ambiguity honestly. Back up first when testing on hardware and remove
only the temporary records created by that test. Never infer an empty configuration from
malformed or missing status data.

## Local operating files

The operator creates `.env` from `.env.example`, or sets `NOKIA_ROUTER_ENV_FILE` to a
private path. `.venv/`, `backups/`, and `probe-output/` are ignored. New exports default
to `~/.nokia-router-mcp/backups`, or the configured private directory. Export files may
contain secrets; diagnostic probes contain identifying network data even after redaction.
Public examples and test fixtures must use synthetic domains and addresses.
