"""Command line entry points.

  nokia-router-mcp            run the MCP server (stdio)
  nokia-router-mcp check      log in once and report success/failure (no secrets printed)
  nokia-router-mcp probe DIR  log in and dump status endpoints (secrets redacted) to DIR
  nokia-router-mcp backup DIR export a configuration backup to a unique file in DIR
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _check() -> int:
    from .client import NokiaRouter, RouterConfig

    cfg = RouterConfig.from_env()
    print(f"Router: {cfg.url}  user: {cfg.username or '(unset)'}  mode: {cfg.login_mode}")
    r = NokiaRouter(cfg)
    try:
        info = r.login()
    except Exception as e:
        print(f"LOGIN FAILED: {e}")
        return 1
    print(f"LOGIN OK: {info}")
    ov = r.get("dashboard_status_web_app.cgi")
    print("overview keys:", list(ov.keys()) if isinstance(ov, dict) else type(ov).__name__)
    r.logout()
    return 0


PROBE_ENDPOINTS = [
    "main_web_app.cgi",
    "device_status_web_app.cgi?getroot",
    "dashboard_status_web_app.cgi",
    "dashboard_device_status_web_app.cgi",
    "lan_status_web_app.cgi?lan",
    "lan_status_web_app.cgi?wlan",
    "device_home_nw_client_status_web_app.cgi",
    "mesh_status_web_app.cgi",
    "show_wan_status_web_app.cgi?ipv4",
    "wan_config_glb_status_web_app.cgi",
    "pon_status_web_app.cgi",
    "wlan_config_status_web_app.cgi",
    "wlan_config_status_web_app.cgi?v=11ac",
    "wlan_config_status_web_app.cgi?v=6g_band",
    "wlan_config_guest_status_web_app.cgi",
    "nat_glb_status_web_app.cgi?v=vhost",
    "nat_glb_status_web_app.cgi?v=alg",
    "lan_ipv4_status_web_app.cgi",
    "dns_status_web_app.cgi",
    "firewall_status_web_app.cgi?fire",
    "statistics_status_web_app.cgi",
    "capabilities_status_web_app.cgi",
    "check_expire_web_app.cgi",
    "nat_glb_status_web_app.cgi?v=thost",
    "domain_route_status_web_app.cgi",
    "route_status_web_app.cgi",
    "device_name_status_web_app.cgi",
    "ddns_status_web_app.cgi",
    "upnp_status_web_app.cgi",
    "macfilter_status_web_app.cgi",
    "ipfilter_status_web_app.cgi",
    "urlfilter_status_web_app.cgi",
    "access_control_status_web_app.cgi",
    "parental_ctrl_status_web_app.cgi",
    "sntp_status_web_app.cgi",
    "qos_status_web_app.cgi",
    "wifi_schedule_status_web_app.cgi",
    "lan_ipv6_status_web_app.cgi",
    "wan_dhcp_status_web_app.cgi",
    "tr69_status_web_app.cgi",
]


def _probe(out_dir: str) -> int:
    from .client import NokiaRouter, RouterConfig
    from .server import _redact

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    r = NokiaRouter(RouterConfig.from_env())
    r.login()
    for ep in PROBE_ENDPOINTS:
        name = ep.replace("?", "__").replace("=", "-").replace("&", "_")
        try:
            data = _redact(r.get(ep))
            (out / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"ok   {ep}")
        except Exception as e:
            print(f"FAIL {ep}: {e}")
    r.logout()
    return 0


def main() -> None:
    args = sys.argv[1:]
    if not args:
        from .server import main as serve

        serve()
        return
    # env is loaded by importing server
    from . import server  # noqa: F401

    if args[0] == "check":
        sys.exit(_check())
    if args[0] == "probe":
        sys.exit(_probe(args[1] if len(args) > 1 else "probe-output"))
    if args[0] == "backup":
        from .operations import RouterOperations
        r = server.router()
        try:
            result = RouterOperations(r).backup_config(args[1] if len(args) > 1 else None)
            print(json.dumps(result, indent=2))
        finally:
            r.logout()
        return
    print(__doc__)
    sys.exit(2)
