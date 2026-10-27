# Security and privacy

This server runs locally over stdio and authenticates to the router's existing web UI.
Run it where the router is reachable over your LAN or a private network route. Do not
expose the router's management interface to the public internet for this integration.

Keep credentials in a private `.env` or your process environment. `.env`, virtual
environments, exported configurations, and diagnostic dumps are excluded from Git.
The public package contains no router credentials or live network inventories.

Configuration exports can contain credentials. On Unix the tool creates them with mode
`0600`; on Windows use a directory with appropriate NTFS permissions. Git ignore rules
only cover the default in-checkout backup/probe directories, so keep custom destinations
outside your checkout. The export is opaque; no claim is made about its encryption at rest.

Status output and write previews can reveal device names, IPs, MACs, and routing rules.
Recognized password fields are redacted in normal reads; `wifi_networks` can explicitly
return passwords when requested. Review logs and probe files before attaching them to
issues. Never attach `.env` or a real configuration export.

Writes are opt-in and previews are the default. `confirm=true` is an API parameter,
not an authentication boundary: configure your agent's permissions to match its intended
authority. Raw POST is a separate opt-in and has fewer safeguards than structured tools.
Read/validate/write/verify operations are serialized within one server process, not
across independent clients.

For a potential security issue, use the repository's private vulnerability reporting
feature when available. Avoid publishing credentials or an exploitable disclosure in
a public issue. For routine compatibility reports, use synthetic examples and provide
the router model, web UI version, tool name, and redacted error.
