# nokia-router-mcp

<p align="center">
  <img src="docs/assets/router-mcp.png" width="680" alt="An agent connects a home router to DNS records and forwarded services">
</p>

Let your AI agent inspect and manage a Nokia WiFi Beacon or gateway router. Add local
DNS entries for new apps, manage port forwards, inspect devices, and save configuration
backups through MCP. Works with Hermes and other clients that support MCP over stdio.

**Read-only by default.** Enable writes explicitly; DNS and port-forward changes preview
before applying, and successful changes are verified against fresh router state.

Independent project, unaffiliated with Nokia. Tested on a **Beacon G6**, web UI version
**2501.20.03.A17**. Other models using `web_whw` are unverified; FastMile login support is
experimental. Requires Python 3.10+ and network access to the router.

## Install

Clone the repository, install into a virtual environment, and create your private `.env`.

**macOS / Linux**

```bash
git clone https://github.com/pbozzay/nokia-router-mcp.git
cd nokia-router-mcp
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp .env.example .env
chmod 600 .env
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/pbozzay/nokia-router-mcp.git
cd nokia-router-mcp
py -3 -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
Copy-Item .env.example .env
```

Edit `.env` with your router's web UI URL, username, and password. The file stays on the
machine running this MCP server and is ignored by Git. Use environment variables instead
if your agent already manages secrets. Then check the connection:

```bash
.venv/bin/nokia-router-mcp check
```

On Windows, use `.venv/Scripts/nokia-router-mcp.exe check`. The check logs in and reads the
overview; it does not change router settings. Its output includes connection information,
so review it before sharing.

## Connect Hermes

Add this entry to `~/.hermes/config.yaml`, replacing both paths with absolute paths on
the machine running Hermes. This enables the structured write tools:

```yaml
mcp_servers:
  nokia_router:
    command: "/absolute/path/to/nokia-router-mcp/.venv/bin/nokia-router-mcp"
    args: []
    env:
      NOKIA_ROUTER_ENV_FILE: "/absolute/path/to/nokia-router-mcp/.env"
      NOKIA_ROUTER_ALLOW_WRITES: "1"
      NOKIA_ROUTER_ALLOW_RAW_WRITES: "0"
```

For Windows, use `C:/path/to/nokia-router-mcp/.venv/Scripts/nokia-router-mcp.exe` and
`C:/path/to/nokia-router-mcp/.env`. Start a new Hermes session or use `/reload-mcp`.
See [Hermes MCP configuration](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp).

Other MCP clients use the same executable, empty arguments, and environment variables
in their stdio server configuration. The executable must come from the virtual environment
where you installed this project.

The MCP process needs LAN access to the router. To manage it remotely, run the agent on a
LAN machine or use a private route to that network. This server uses stdio; it does not
listen for incoming network connections.

## Manage a new app

Ask your agent to inspect `dns_config` and `port_forward_rules`, then preview the change.
For example, `set_dns_entry` accepts:

```json
{"hostname": "app.example.test", "ipv4_address": "192.168.1.20"}
```

If the app needs a new inbound mapping, `add_port_forward` accepts:

```json
{"external_port": 8443, "internal_ip": "192.168.1.20", "internal_port": 443, "protocol": "TCP"}
```

After inspecting the preview, repeat the call with `"confirm": true` to apply the
authorized change. `applied` means read-back verified the desired state; `unchanged` means
it already existed. On `unknown` or `unverified`, inspect current state before retrying.

Local DNS affects clients using the router's resolver. Cloudflare public DNS and Nginx
Proxy Manager configuration are separate. An app behind an existing reverse proxy may
only need a local DNS entry pointing to that proxy.

## Available tools

| Capability | Tools |
|---|---|
| Router and clients | `router_info`, `overview`, `list_devices`, `mesh_status` |
| Network status | `wan_status`, `optical_status`, `wifi_networks`, `lan_dhcp` |
| Configuration and diagnostics | `dns_config`, `port_forwards`, `port_forward_rules`, `firewall_status`, `traffic_statistics`, `system_log`, `read_endpoint` |
| Local IPv4 DNS writes | `set_dns_entry`, `delete_dns_entry` |
| Static port-forward writes | `add_port_forward`, `delete_port_forward` |
| Maintenance | `backup_config`, `reboot_router` |

The first 15 tools are always available. The last six require
`NOKIA_ROUTER_ALLOW_WRITES=1`. WiFi passwords are hidden unless explicitly requested.
Raw `post_endpoint` requires an additional `NOKIA_ROUTER_ALLOW_RAW_WRITES=1` opt-in.

DNS and forwarding writes are idempotent, validate current configuration, and avoid
replaying a POST after timeouts or ambiguous responses. Port forwards support TCP, UDP,
combined TCP/UDP, ranges, WAN selection, and overlap checks. DNS replacement can restore
the previous entry after a definite rejection with verified absence. DNS proxy must
already be enabled; changing that setting can reboot the router and is not exposed.

See the [tool reference](docs/tools.md) for arguments, result states, limitations, and
backup handling. Restore, firmware updates, WiFi changes, DHCP reservations, and DNS
aliases are not structured tools in this release.

## Configuration

Existing process environment variables take precedence. Missing values are loaded from
`NOKIA_ROUTER_ENV_FILE`, then the current directory's `.env`, then the source checkout's
`.env`. Set an explicit file path for reliable agent startup from any working directory.

| Variable | Default | Purpose |
|---|---|---|
| `NOKIA_ROUTER_URL` | `http://192.168.1.1` | Router web UI base URL; the example uses `http://www.webgui.nokiawifi.com` |
| `NOKIA_ROUTER_USERNAME` | empty | Web UI username |
| `NOKIA_ROUTER_PASSWORD` | empty | Web UI password |
| `NOKIA_ROUTER_LOGIN_MODE` | `standard` | `fwa` is experimental FastMile login |
| `NOKIA_ROUTER_ALLOW_WRITES` | `0` | Expose structured writes, backups, and reboot |
| `NOKIA_ROUTER_ALLOW_RAW_WRITES` | `0` | Also expose raw POST; requires writes enabled |
| `NOKIA_ROUTER_BACKUP_DIR` | `~/.nokia-router-mcp/backups` | Private export directory; an explicit CLI destination takes precedence |
| `NOKIA_ROUTER_TIMEOUT` | `60` | Timeout in seconds for each HTTP request |

Credentials, exported configurations, and router inventories are local data. Never commit
your `.env`, `backups/`, or `probe-output/`. Backups may contain credentials. Probe output
redacts recognized secret fields but still contains network addresses and device identities.
See [security and privacy](SECURITY.md).

## Development and verification

```bash
.venv/bin/python -m unittest discover -v
.venv/bin/python -m pip install build
.venv/bin/python -m build
```

On Windows use `.venv/Scripts/python.exe`. Tests use synthetic router state and do not
connect to a real router. CI covers Python 3.10 and 3.12 on macOS, Linux, and Windows,
including MCP discovery from the installed wheel.

Hardware checks on the Beacon G6 covered configuration export, DNS create/replace/delete
with direct DNS resolution, and a TCP forward's add/delete cycle. Existing DNS and static
forward state matched the baseline after cleanup. External WAN traffic, additional router
models, and restore were not tested. See [protocol notes](docs/research/write-forms.md),
[maintainer handoff](HANDOFF.md), and [release notes](CHANGELOG.md).
