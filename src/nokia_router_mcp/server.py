"""MCP server exposing a Nokia WiFi Beacon / gateway as tools."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import NokiaRouter, RouterConfig
from .operations import RouterOperations
from .port_forwarding import list_rules
from .write_support import request


def _load_env() -> None:
    explicit = os.environ.get("NOKIA_ROUTER_ENV_FILE")
    if explicit:
        load_dotenv(explicit, override=False)
    load_dotenv(Path.cwd() / ".env", override=False)
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


_load_env()

ALLOW_WRITES = os.environ.get("NOKIA_ROUTER_ALLOW_WRITES", "0") == "1"
ALLOW_RAW_WRITES = os.environ.get("NOKIA_ROUTER_ALLOW_RAW_WRITES", "0") == "1"

mcp = FastMCP(
    "nokia-router",
    instructions=(
        "Tools for a Nokia WiFi Beacon / gateway router (web UI 'web_whw'). "
        "Read tools are safe. Write tools change live router config and briefly "
        "disrupt the network for some operations (reboot, WiFi changes). Structured "
        "writes preview by default; use confirm=true for changes the user authorized. "
        "Prefer set_dns_entry/add_port_forward over raw POST. Inspect status: only applied "
        "or unchanged confirms the desired state; unknown/unverified requires reading state "
        "before any retry. Local DNS overrides do not configure public DNS or reverse proxies."
    ),
)

_router: NokiaRouter | None = None
_router_lock = threading.Lock()


def router() -> NokiaRouter:
    global _router
    with _router_lock:
        if _router is None:
            _router = NokiaRouter(RouterConfig.from_env())
    return _router


READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

_SECRET_KEY = re.compile(r"(password|passphrase|presharedkey|psk|wepkey|keypass|secret|pin$)", re.I)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("***" if _SECRET_KEY.search(k) and isinstance(v, (str, int)) and v != "" else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _get(endpoint: str) -> Any:
    return _redact(router().get(endpoint))


# Endpoints the raw reader may call. Pure status reads only — some *_status
# endpoints take action verbs (e.g. speed_test_status_web_app.cgi?start), so those are blocked.
_SAFE_GET = re.compile(r"^[a-z0-9_]+_status_web_app\.cgi(\?[A-Za-z0-9_=&.\-]*)?$|^main_web_app\.cgi$", re.I)
_UNSAFE_VERB = re.compile(r"(start|cancel|del|add|set|reset|reboot|restore|trigger)", re.I)


# =========================================================================== read tools


@mcp.tool(annotations=READ)
def router_info() -> dict:
    """Model, operator, firmware capabilities and root device identity of the router."""
    r = router()
    return {"main": _redact(r.get("main_web_app.cgi")), "device": _get("device_status_web_app.cgi?getroot")}


@mcp.tool(annotations=READ)
def overview() -> Any:
    """Dashboard summary: WAN up/down + external IPs, LAN IP, WiFi radio state."""
    return _get("dashboard_status_web_app.cgi")


@mcp.tool(annotations=READ)
def list_devices() -> dict:
    """Client devices known to the router (hostnames, IP/MAC, active state, WiFi vs ethernet)."""
    return {
        "devices": _get("dashboard_device_status_web_app.cgi"),
        "lan": _get("lan_status_web_app.cgi?lan"),
    }


@mcp.tool(annotations=READ)
def mesh_status() -> Any:
    """Mesh / Beacon satellite nodes and their backhaul."""
    return _get("mesh_status_web_app.cgi")


@mcp.tool(annotations=READ)
def wan_status(include_ipv6: bool = False) -> dict:
    """Internet (WAN) connection state, IP config, link speed. Optionally IPv6 too."""
    out = {"ipv4": _get("show_wan_status_web_app.cgi?ipv4")}
    if include_ipv6:
        out["ipv6"] = _get("show_wan_status_web_app.cgi?ipv6")
    return out


@mcp.tool(annotations=READ)
def optical_status() -> Any:
    """Fiber/PON optical levels (rx/tx power) if the gateway has a PON port."""
    return _get("pon_status_web_app.cgi")


_BANDS = {
    "2.4GHz": "wlan_config_status_web_app.cgi",
    "5GHz": "wlan_config_status_web_app.cgi?v=11ac",
    "5GHz-high": "wlan_config_status_web_app.cgi?v=11ac_highband",
    "6GHz": "wlan_config_status_web_app.cgi?v=6g_band",
}


@mcp.tool(annotations=READ)
def wifi_networks(include_passwords: bool = False) -> dict:
    """WiFi SSIDs per radio band plus the guest network. Passwords are hidden unless include_passwords=True."""
    r = router()
    out: dict[str, Any] = {}
    for band, ep in _BANDS.items():
        try:
            data = r.get(ep)
        except Exception as e:  # band not present on this model
            out[band] = {"error": str(e)}
            continue
        if include_passwords:
            _decrypt_psks(r, data)
            out[band] = data
        else:
            out[band] = _redact(data)
    guest = r.get("wlan_config_guest_status_web_app.cgi")
    out["guest"] = guest if include_passwords else _redact(guest)
    return out


def _decrypt_psks(r: NokiaRouter, node: Any) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "PreSharedKey" and isinstance(v, str):
                node[k] = r.decrypt_value(v)
            else:
                _decrypt_psks(r, v)
    elif isinstance(node, list):
        for v in node:
            _decrypt_psks(r, v)


@mcp.tool(annotations=READ)
def port_forwards() -> Any:
    """Port-forwarding (virtual server) rules, per WAN connection."""
    return _get("nat_glb_status_web_app.cgi?v=vhost")


@mcp.tool(annotations=READ)
def port_forward_rules() -> dict:
    """Normalized port forwards with WAN ids, ranges, protocols and static/UPnP sources.
    Use these fields with add_port_forward/delete_port_forward; raw status is in port_forwards."""
    return list_rules(router())


@mcp.tool(annotations=READ)
def lan_dhcp() -> Any:
    """LAN IPv4 / DHCP server settings and static DHCP reservations."""
    return _get("lan_ipv4_status_web_app.cgi")


@mcp.tool(annotations=READ)
def dns_config() -> Any:
    """DNS settings and local DNS entries."""
    return _get("dns_status_web_app.cgi")


@mcp.tool(annotations=READ)
def firewall_status() -> dict:
    """Firewall level, DMZ/ALG, UPnP state."""
    return {
        "firewall": _get("firewall_status_web_app.cgi?fire"),
        "dmz_alg": _get("nat_glb_status_web_app.cgi?v=alg"),
        "upnp": _get("upnp_status_web_app.cgi"),
    }


@mcp.tool(annotations=READ)
def traffic_statistics() -> Any:
    """Per-interface traffic counters (bytes/packets/errors) for WAN, LAN and WiFi."""
    return _get("statistics_status_web_app.cgi")


@mcp.tool(annotations=READ)
def system_log() -> Any:
    """Router event log."""
    return _get("log_status_web_app.cgi?vlog_glb")


@mcp.tool(annotations=READ)
def read_endpoint(endpoint: str) -> Any:
    """Read any *_status_web_app.cgi endpoint of the web UI (e.g. 'route_status_web_app.cgi',
    'macfilter_status_web_app.cgi', 'wifi_schedule_status_web_app.cgi'). Secrets are redacted."""
    ep = endpoint.lstrip("/")
    if not _SAFE_GET.match(ep) or _UNSAFE_VERB.search(ep.split("?", 1)[1] if "?" in ep else ""):
        raise ValueError("Only pure *_status_web_app.cgi reads (no action verbs) are allowed here.")
    return _get(ep)


# =========================================================================== write tools

if ALLOW_WRITES:

    @mcp.tool(annotations=WRITE)
    def set_dns_entry(hostname: str, ipv4_address: str, confirm: bool = False) -> dict:
        """Ensure a local DNS hostname points to an IPv4 address. Preview by default.
        Replacements delete then add; definite rejection triggers restoration of the old entry.
        The DNS proxy must already be enabled. Does not change public DNS or reboot."""
        return RouterOperations(router()).set_dns_entry(hostname, ipv4_address, confirm)

    @mcp.tool(annotations=WRITE)
    def delete_dns_entry(hostname: str, confirm: bool = False) -> dict:
        """Remove a local DNS hostname, or do nothing if absent. Preview by default.
        Router management hostnames are protected."""
        return RouterOperations(router()).delete_dns_entry(hostname, confirm)

    @mcp.tool(annotations=WRITE)
    def add_port_forward(external_port: int, internal_ip: str, internal_port: int,
                         protocol: str = "TCP", external_end: int | None = None,
                         wan: str | None = None, confirm: bool = False) -> dict:
        """Add a static IPv4 port forward, checking conflicts and verifying state afterwards.
        Preview by default. Protocol: TCP, UDP or TCP/UDP. external_end creates a range;
        the internal range has the same length. Select WAN by id/name from port_forward_rules
        when more than one exists. Identical enabled static rules are left unchanged."""
        return RouterOperations(router()).add_port_forward(external_port, internal_ip, internal_port,
                                                           protocol, external_end, wan, confirm)

    @mcp.tool(annotations=WRITE)
    def delete_port_forward(external_port: int, internal_ip: str, internal_port: int,
                            protocol: str = "TCP", external_end: int | None = None,
                            wan: str | None = None, confirm: bool = False,
                            internal_end: int | None = None, remote_host: str | None = None) -> dict:
        """Delete one exactly matching static forward. Preview by default. Match the full
        port range, target, protocol and WAN from port_forward_rules. UPnP/clone rules are
        excluded. For an existing unequal internal range or source filter, also supply
        internal_end and remote_host from port_forward_rules. An absent rule is unchanged."""
        return RouterOperations(router()).delete_port_forward(external_port, internal_ip, internal_port,
                                                              protocol, external_end, wan, confirm, internal_end, remote_host)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    def backup_config() -> dict:
        """Export router configuration to a unique local .cfg file. Returns path, size and
        checksum only; the file may contain secrets. Directory: NOKIA_ROUTER_BACKUP_DIR,
        defaulting to ~/.nokia-router-mcp/backups. Does not change router settings."""
        return RouterOperations(router()).backup_config()

    @mcp.tool(annotations=WRITE)
    def reboot_router(confirm: bool = False) -> Any:
        """Reboot the router. Drops internet for a few minutes. Requires confirm=True."""
        if not confirm:
            return {"status": "preview", "requests": [request("reboot_web_app.cgi")],
                    "message": "Drops internet for a few minutes. Pass confirm=True to reboot."}
        return router().post("reboot_web_app.cgi")

if ALLOW_WRITES and ALLOW_RAW_WRITES:

    @mcp.tool(annotations=WRITE)
    def post_endpoint(endpoint: str, form_body: str = "", confirm: bool = False) -> Any:
        """Send a raw config change to a *_web_app.cgi endpoint, exactly as the web UI would
        (form-encoded body; CSRF token and encryption are added automatically).
        Requires the separate NOKIA_ROUTER_ALLOW_RAW_WRITES=1 opt-in. Does not validate
        feature semantics or verify results. Factory reset / firmware / password endpoints
        are blocked. Preview by default; requires confirm=True to send."""
        ep = endpoint.removeprefix("/")
        if not re.fullmatch(r"[a-z0-9_]+_web_app\.cgi(?:\?[A-Za-z0-9_=&.,:%+\-]*)?", ep, re.I):
            raise ValueError("Use a router-relative *_web_app.cgi endpoint with no path or fragment.")
        blocked = ("restore", "upgrade", "firmware", "password", "user_mgmt",
                   "usb", "login", "command", "service_function")
        if any(b in ep.split("?", 1)[0].lower() for b in blocked):
            raise ValueError(f"{ep} is blocked from the MCP; do this in the web UI.")
        if not confirm:
            return {"status": "preview", "requests": [{"endpoint": ep, "form_body": form_body or None}]}
        return router().post(ep, form_body or None)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
