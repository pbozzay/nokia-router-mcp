"""Structured changes built from the Beacon G6 web UI's forms."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .client import NokiaRouter, RouterError
from .write_support import apply_once, hostname, ipv4, object_id, request, result


class RouterOperations:
    def __init__(self, router: NokiaRouter, verify_delays: tuple[float, ...] = (0.5, 1.5)):
        self.router = router
        self.verify_delays = verify_delays

    def _apply(self, req, read, satisfied):
        return apply_once(self.router, req, read, satisfied, self.verify_delays)

    def _dns_state(self):
        data = self.router.get("dns_status_web_app.cgi")
        try:
            if not isinstance(data["dns_cfg"], list) or not isinstance(data["domain_cfg"], list):
                raise ValueError()
            if data["proxy_cfg"]["Enable"] not in (0, 1, "0", "1"):
                raise ValueError()
            for row in data["dns_cfg"]:
                object_id(row["_oid"])
                hostname(row["domainName"])
                ipv4(row["domainAddr"])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise RouterError("DNS status is incomplete or malformed; refusing to guess.") from exc
        return data

    @staticmethod
    def _dns_entry(state, name):
        rows = [row for row in state["dns_cfg"] if hostname(row["domainName"]) == name]
        if len(rows) > 1:
            raise RouterError("Multiple DNS entries have this hostname; resolve them in the UI.")
        return rows[0] if rows else None

    def _read_dns_entry(self, name):
        return self._dns_entry(self._dns_state(), name)

    @staticmethod
    def _dns_name(value):
        name = hostname(value)
        if name in ("webgui.nokiawifi.com", "www.webgui.nokiawifi.com"):
            raise ValueError("The router's management DNS entry is protected.")
        return name

    def set_dns_entry(self, name: str, address: str, confirm: bool = False) -> dict:
        name, address = self._dns_name(name), ipv4(address)
        with self.router.transaction():
            state = self._dns_state()
            if str(state["proxy_cfg"]["Enable"]) != "1":
                raise RouterError("DNS proxy is disabled. Enabling it requires a reboot in the web UI.")
            before = self._dns_entry(state, name)
            if before and before["domainAddr"] == address:
                return result("unchanged", before, before, [])
            add = request("dns_web_app.cgi?add", {"domainName": name, "domainAddr": address})
            reqs = [add]
            if before:
                reqs.insert(0, request("dns_web_app.cgi?act=del&oid=" + object_id(before["_oid"])))
            if not confirm:
                return result("preview", before, {"domainName": name, "domainAddr": address}, reqs)
            read = lambda: self._read_dns_entry(name)
            if before:
                deletion = self._apply(reqs[0], read, lambda row: row is None)
                if deletion["status"] != "applied":
                    return {**result(deletion["status"], before, deletion["after"], reqs[:1]), **deletion}
            outcome = self._apply(add, read, lambda row: row is not None and row["domainAddr"] == address)
            # Restore only after a definite rejection and a successful read proving absence.
            # A timed-out request may still be processing, so it must not trigger rollback.
            if before and outcome["status"] == "rejected" and outcome["after"] is None:
                restore = request("dns_web_app.cgi?add", {"domainName": before["domainName"], "domainAddr": before["domainAddr"]})
                rollback = self._apply(restore, read, lambda row: row is not None and row["domainAddr"] == before["domainAddr"])
                reqs.append(restore)
                status = "rolled_back" if rollback["status"] == "applied" else "rollback_failed"
                return result(status, before, rollback["after"], reqs,
                              message="Replacement was rejected; restoration of the previous entry was attempted.", rollback=rollback)
            return {**result(outcome["status"], before, outcome["after"], reqs), **outcome}

    def delete_dns_entry(self, name: str, confirm: bool = False) -> dict:
        name = self._dns_name(name)
        with self.router.transaction():
            before = self._read_dns_entry(name)
            if before is None:
                return result("unchanged", None, None, [])
            req = request("dns_web_app.cgi?act=del&oid=" + object_id(before["_oid"]))
            if not confirm:
                return result("preview", before, None, [req])
            outcome = self._apply(req, lambda: self._read_dns_entry(name), lambda row: row is None)
            return {**result(outcome["status"], before, outcome["after"], [req]), **outcome}

    def backup_config(self, directory: str | Path | None = None) -> dict:
        if directory is None:
            directory = os.environ.get("NOKIA_ROUTER_BACKUP_DIR") or Path.home() / ".nokia-router-mcp" / "backups"
        with self.router.transaction():
            content = self.router.get_bytes("usb_web_app.cgi?export")
        head = content.lstrip().lower()
        if not head or head.startswith((b"<html", b"<!doctype html", b"{", b"[")):
            raise RouterError("Export was empty or an HTML/JSON response; no backup was saved.")
        directory = Path(directory).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = directory / f"nokia-{stamp}-{uuid4().hex[:8]}.cfg"
        # Exclusive creation avoids overwrites; mode limits access on Unix.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(content)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {"path": str(path), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    def add_port_forward(self, external_port: int, internal_ip: str, internal_port: int,
                         protocol: str = "TCP", external_end: int | None = None,
                         wan: str | None = None, confirm: bool = False) -> dict:
        from .port_forwarding import change_forward
        return change_forward(self, False, external_port, internal_ip, internal_port, protocol, external_end, wan, confirm)

    def delete_port_forward(self, external_port: int, internal_ip: str, internal_port: int,
                            protocol: str = "TCP", external_end: int | None = None,
                            wan: str | None = None, confirm: bool = False,
                            internal_end: int | None = None, remote_host: str | None = None) -> dict:
        from .port_forwarding import change_forward
        return change_forward(self, True, external_port, internal_ip, internal_port, protocol, external_end,
                              wan, confirm, internal_end, remote_host)
