# Tool reference

Enable `NOKIA_ROUTER_ALLOW_WRITES=1` before starting the server to expose these tools.
DNS and port-forward tools default to `confirm=false`: inspect the proposed state and
requests, then set `confirm=true` for an authorized change. Revalidation uses fresh state
at application time. A preview does not reserve the configuration against other clients.

## Local DNS

| Tool | Arguments |
|---|---|
| `set_dns_entry` | `hostname`, `ipv4_address`, `confirm=false` |
| `delete_dns_entry` | `hostname`, `confirm=false` |

Hostnames are normalized to lowercase without a trailing dot; wildcard and non-ASCII
names are rejected. Only IPv4 address overrides are supported. The router's management
hostnames are protected. DNS proxy must already be enabled.

Setting an existing hostname to the same address is a no-op. Changing its address uses
delete followed by add because the firmware has no edit operation. If the add is definitely
rejected and a read confirms absence, the tool attempts to restore the previous address.
Ambiguous writes never trigger automatic replay or rollback. Deleting an absent entry is
a no-op. Aliases and proxy toggles are not exposed.

## Port forwarding

`add_port_forward(external_port, internal_ip, internal_port, protocol="TCP",
external_end=null, wan=null, confirm=false)`

Ports must be in 1–65535. `external_end` adds an inclusive range, and the internal range
has the same length starting at `internal_port`. Protocol accepts TCP, UDP, or TCP/UDP.
The target must be a usable host on the reported LAN, excluding the router address,
network address, and broadcast address.

Use `port_forward_rules` to see normalized rules and WAN identifiers. With multiple
routed IPv4 WANs, select `wan` explicitly. Conflicts are checked by WAN, port range, and
protocol across static mappings, UPnP, and port triggers. An identical enabled static
rule is a no-op. Firmware custom-application forms do not accept a custom rule name.

`delete_port_forward` accepts the same arguments, plus `internal_end=null` and
`remote_host=null` for existing rules with unequal ranges or source filters. Copy the
complete identity from `port_forward_rules`. Deletion uses the composite WAN/rule ID,
excludes dynamic/clone mappings, and reports incomplete matches rather than silently
claiming absence. A fully specified rule that is absent is a no-op.

## Result states

| Status | Meaning |
|---|---|
| `preview` | No write sent; `after` is proposed state |
| `applied` | Desired state verified by reading the router |
| `unchanged` | Desired state already exists; no write sent |
| `rejected` | Router rejected the request and desired state was not observed |
| `unverified` / `unknown` | Desired state was not confirmed; inspect state before retrying |
| `rolled_back` | DNS replacement failed; previous entry restored and verified |
| `rollback_failed` | Restoration was not verified; inspect `before`, `after`, and `rollback` |

Results include before/after state and endpoint/form intent without CSRF or encryption
secrets. They can still contain network configuration: treat them as private operational
data. Success is determined by read-back, not just the router's POST response.

## Backups and reboot

`backup_config()` immediately exports to a unique local `.cfg` file. It does not change
router settings and returns only the path, byte count, and SHA-256. Exports default to
`~/.nokia-router-mcp/backups`; set `NOKIA_ROUTER_BACKUP_DIR` to choose a private directory.
Tilde paths are expanded for both the environment setting and CLI destination.

Files may contain credentials. Unix exports use mode `0600`; on Windows, access follows
the destination directory's NTFS permissions. Files are created exclusively and never
overwrite an existing export. Restore remains a manual web-UI operation.

The CLI can export without enabling MCP writes:

```bash
.venv/bin/nokia-router-mcp backup /absolute/private/router-backups
```

On Windows use `.venv/Scripts/nokia-router-mcp.exe backup C:/private/router-backups`.
An explicit CLI destination takes precedence over `NOKIA_ROUTER_BACKUP_DIR`; omitting both
uses the same private user directory as the MCP tool.

`reboot_router(confirm=false)` previews; `confirm=true` sends the reboot request once.
It interrupts connectivity and does not verify reboot completion.

## Raw endpoints and diagnostics

`read_endpoint` is restricted to pure status endpoints. Some firmware endpoints named
“status” also accept action verbs, so they are deliberately excluded.

`post_endpoint(endpoint, form_body="", confirm=false)` requires both write flags. It has
no feature-level validation or read-back verification. Factory reset/restore, firmware,
password, backup/import, login, and shell endpoint families are blocked. Use structured
tools for routine configuration. A raw preview does not establish that an arbitrary
firmware operation is safe.

`nokia-router-mcp probe probe-output/` saves status JSON with recognized secret fields
redacted. Inspect and anonymize it before sharing; names, MACs, public IPs, and topology
remain in the output. Router endpoint support and response times vary by firmware.
