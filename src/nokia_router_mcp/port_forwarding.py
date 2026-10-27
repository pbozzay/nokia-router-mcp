"""NAT form encoding and rule selection for the G6 web UI."""
from __future__ import annotations

import ipaddress

from .client import RouterError
from .write_support import ipv4, object_id, request, result


def _port(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError("Ports must be integers between 1 and 65535.")
    return value


def _protocol(value):
    protocols = {"TCP": "TCP", "UDP": "UDP", "TCP/UDP": "TCPorUDP", "TCPORUDP": "TCPorUDP"}
    try:
        return protocols[value.upper()]
    except (KeyError, AttributeError):
        raise ValueError("Protocol must be TCP, UDP or TCP/UDP.") from None


def _protocols_overlap(a, b):
    return a == b or "TCPorUDP" in (a, b)


def _connections(data, section):
    if not isinstance(data[section], list):
        raise ValueError("Invalid WAN collection")
    for group in data[section]:
        did, gid = object_id(group["did"]), object_id(group["_oid"])
        for kind, key in (("ip", "ipConns"), ("ppp", "pppConns")):
            if not isinstance(group[key], list):
                raise ValueError("Invalid WAN connections")
            for conn in group[key]:
                cid = object_id(conn["_oid"])
                yield f"{kind},{did},{gid},{cid}", conn


def _range(row, start_key, end_key):
    # The UI normalizes a zero on either side to the other bound.
    start, end = int(row[start_key]), int(row[end_key])
    start, end = start or end, end or start
    _port(start)
    _port(end)
    if start > end:
        raise ValueError("Reversed port range in router state")
    return start, end


def _state(router):
    data = router.get("nat_glb_status_web_app.cgi?v=vhost")
    try:
        lan = ipaddress.IPv4Interface(f"{data['lan_ifip']['IPInterfaceIPAddress']}/{data['lan_ifip']['IPInterfaceSubnetMask']}")
        wans, rules, triggers = [], [], []
        for key, conn in _connections(data, "port_mappings"):
            if conn["ConnectionType"] in ("IP_Routed", "PPPoE_Routed") and int(conn["X_CT_COM_IPMode"]) in (1, 3):
                wans.append({"id": key, "name": conn["Name"]})
            if not isinstance(conn["portmaps"], list):
                raise ValueError("Invalid port mappings")
            for row in conn["portmaps"]:
                ext, ext_end = _range(row, "ExternalPort", "X_ASB_COM_ExternalPortEnd")
                inner, inner_end = _range(row, "InternalPort", "X_ASB_COM_InternalPortEnd")
                enabled = int(row["PortMappingEnabled"])
                if enabled not in (0, 1):
                    raise ValueError("Invalid enabled flag")
                rules.append({"wan": key, "id": object_id(row["_oid"]), "external_port": ext,
                              "external_end": ext_end, "internal_port": inner, "internal_end": inner_end,
                              "internal_ip": ipv4(row["InternalClient"]), "protocol": _protocol(row["PortMappingProtocol"]),
                              "enabled": bool(enabled), "source": row.get("X_ASB_COM_Cfg_Source", "").strip() or "static",
                              "remote_host": _remote_host(row.get("RemoteHost", ""))})
        for key, conn in _connections(data, "port_triggerings"):
            if not isinstance(conn["porttriggerings"], list):
                raise ValueError("Invalid port triggers")
            for row in conn["porttriggerings"]:
                start, end = _range(row, "OpenPortStart", "OpenPortEnd")
                triggers.append({"wan": key, "external_port": start, "external_end": end,
                                 "protocol": _protocol(row["OpenProtocol"])})
        return lan, wans, rules, triggers
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise RouterError("Port-forward status is incomplete or malformed; refusing to guess.") from exc


def list_rules(router):
    _, wans, rules, _ = _state(router)
    return {"wans": wans, "rules": rules}


def _select_wan(wans, selection):
    matches = wans if selection is None else [w for w in wans if selection in (w["id"], w["name"])]
    if len(matches) != 1:
        raise ValueError(f"Choose one routed IPv4 WAN by id or name: {wans}")
    return matches[0]["id"]


def _matches(row, wanted):
    return all(row[key] == value for key, value in wanted.items())


def _remote_host(value):
    value = value.strip()
    return "" if value in ("", "0.0.0.0") else ipv4(value)


def change_forward(ops, delete, external_port, internal_ip, internal_port, protocol, external_end, wan, confirm,
                   requested_internal_end=None, remote_host=None):
    ext, inner = _port(external_port), _port(internal_port)
    end = ext if external_end is None else _port(external_end)
    if end < ext:
        raise ValueError("External end port must be at least the start port.")
    inner_end = _port(inner + end - ext) if requested_internal_end is None else _port(requested_internal_end)
    if inner_end < inner:
        raise ValueError("Internal end port must be at least the start port.")
    address, proto = ipv4(internal_ip), _protocol(protocol)
    with ops.router.transaction():
        lan, wans, rules, triggers = _state(ops.router)
        selected = _select_wan(wans, wan)
        ip = ipaddress.IPv4Address(address)
        if not delete and (ip not in lan.network or ip in (lan.ip, lan.network.network_address, lan.network.broadcast_address)):
            raise ValueError("Forwarding target must be a host in the router's LAN subnet, excluding the router.")
        wanted = {"wan": selected, "external_port": ext, "external_end": end, "internal_port": inner,
                  "internal_end": inner_end, "internal_ip": address, "protocol": proto,
                  "remote_host": _remote_host(remote_host or "")}
        exact = [row for row in rules if _matches(row, wanted)]
        static = [row for row in exact if row["source"] == "static"]
        if len(static) > 1:
            raise RouterError("Multiple static rules match; resolve them in the web UI.")
        if delete:
            if not static:
                if exact:
                    raise ValueError("Only static port forwards can be deleted; this mapping is managed elsewhere.")
                omitted = set()
                if requested_internal_end is None:
                    omitted.add("internal_end")
                if remote_host is None:
                    omitted.add("remote_host")
                partial = {key: value for key, value in wanted.items() if key not in omitted}
                if any(_matches(row, partial) for row in rules):
                    raise ValueError("A similar rule has a different internal end or remote host. Copy both from port_forward_rules.")
                return result("unchanged", None, None, [])
            before = static[0]
            kind, did, gid, cid = selected.split(",")
            req = request(f"nat_glb_web_app.cgi?v=del_vhost&t={kind}&id={did},{gid},{cid},{before['id']}")
        else:
            if static and static[0]["enabled"]:
                return result("unchanged", static[0], static[0], [])
            for row in [*rules, *triggers]:
                if row["wan"] == selected and _protocols_overlap(proto, row["protocol"]) and ext <= row["external_end"] and end >= row["external_port"]:
                    raise ValueError(f"Port range conflicts with an existing mapping/trigger: {row}")
            before = None
            req = request("nat_glb_web_app.cgi?v=add_vhost", {
                "appname": "0", "nat_value": "0", "etPortStart": ext, "etPortEnd": end,
                "inPortStart": inner, "inPortEnd": inner_end, "servername": "0", "serverIp": address,
                "proto": proto, "wanif": selected, "en_map": "on",
            })
        if not confirm:
            return result("preview", before, None if delete else wanted, [req])

        def read():
            _, current_wans, current_rules, _ = _state(ops.router)
            if not any(w["id"] == selected for w in current_wans):
                raise RouterError("Selected WAN disappeared while verifying the change.")
            matches = [row for row in current_rules if row["source"] == "static" and _matches(row, wanted)]
            if len(matches) > 1:
                raise RouterError("Multiple static rules match after the change.")
            return matches[0] if matches else None

        satisfied = (lambda row: row is None) if delete else (lambda row: row is not None and row["enabled"])
        outcome = ops._apply(req, read, satisfied)
        return {**result(outcome["status"], before, outcome["after"], [req]), **outcome}
