"""Behavioral contracts derived from the G6 UI, using an in-memory router."""
import copy
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import requests

from nokia_router_mcp import client


class MemoryRouter:
    def __init__(self):
        self.dns = {"proxy_cfg": {"Enable": 1}, "dns_cfg": [
            {"_oid": 1, "domainName": "webgui.nokiawifi.com", "domainAddr": "192.168.1.1"},
            {"_oid": 2, "domainName": "app.example.test", "domainAddr": "192.168.1.9"},
        ], "domain_cfg": []}
        self.nat = {"lan_ifip": {"IPInterfaceIPAddress": "192.168.1.1", "IPInterfaceSubnetMask": "255.255.255.0"},
                    "port_mappings": [{"did": 1, "_oid": 1, "ipConns": [
                        {"_oid": 1, "Name": "Internet", "ConnectionType": "IP_Routed", "X_CT_COM_IPMode": 3, "portmaps": []}
                    ], "pppConns": []}], "port_triggerings": []}
        self.posts = []
        self.effects = []
        self.export = b"NOKIA\x00encrypted configuration\xff"
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self):
        with self._lock:
            yield

    def get(self, endpoint):
        if endpoint == "dns_status_web_app.cgi":
            return copy.deepcopy(self.dns)
        if endpoint == "nat_glb_status_web_app.cgi?v=vhost":
            return copy.deepcopy(self.nat)
        raise AssertionError(endpoint)

    def get_bytes(self, endpoint):
        assert endpoint == "usb_web_app.cgi?export"
        return self.export

    def post(self, endpoint, payload=None):
        self.posts.append((endpoint, payload))
        effect = self.effects.pop(0) if self.effects else "apply"
        if effect == "reject":
            return {"result": 1, "ret": 4}
        if effect == "timeout_before":
            raise requests.Timeout("write timed out")
        if effect == "ignore":
            return {"result": 0}
        form = {k: v[0] for k, v in parse_qs(payload or "").items()}
        query = parse_qs(urlsplit(endpoint).query)
        if endpoint == "dns_web_app.cgi?add":
            assert set(form) == {"domainName", "domainAddr"}
            self.dns["dns_cfg"].append({"_oid": 1 + max(x["_oid"] for x in self.dns["dns_cfg"]), **form})
        elif endpoint.startswith("dns_web_app.cgi?act=del&oid="):
            self.dns["dns_cfg"] = [x for x in self.dns["dns_cfg"] if str(x["_oid"]) != query["oid"][0]]
        elif endpoint == "nat_glb_web_app.cgi?v=add_vhost":
            assert set(form) == {"appname", "nat_value", "etPortStart", "etPortEnd", "inPortStart", "inPortEnd", "servername", "serverIp", "proto", "wanif", "en_map"}
            assert (form["appname"], form["nat_value"], form["servername"], form["wanif"], form["en_map"]) == ("0", "0", "0", "ip,1,1,1", "on")
            self.maps.append({"_oid": 9, "PortMappingEnabled": 1, "ExternalPort": int(form["etPortStart"]),
                              "X_ASB_COM_ExternalPortEnd": int(form["etPortEnd"]), "InternalPort": int(form["inPortStart"]),
                              "X_ASB_COM_InternalPortEnd": int(form["inPortEnd"]), "InternalClient": form["serverIp"],
                              "PortMappingProtocol": form["proto"]})
        elif endpoint.startswith("nat_glb_web_app.cgi?v=del_vhost&t=ip&id=1,1,1,"):
            oid = int(query["id"][0].split(",")[-1])
            self.maps[:] = [x for x in self.maps if x["_oid"] != oid or x.get("X_ASB_COM_Cfg_Source") == "UPnP"]
        else:
            raise AssertionError(endpoint)
        if effect == "timeout_after":
            raise requests.Timeout("reply lost")
        return {"result": 0}

    @property
    def maps(self):
        return self.nat["port_mappings"][0]["ipConns"][0]["portmaps"]


class WriteTests(unittest.TestCase):
    def setUp(self):
        # Missing feature produces a clear assertion until the implementation exists.
        try:
            from nokia_router_mcp import operations
        except ImportError:
            self.fail("RouterOperations has not been implemented")
        self.ops = operations.RouterOperations(MemoryRouter(), verify_delays=(0, 0))
        self.r = self.ops.router

    def test_dns_preview_has_exact_request_without_posting(self):
        result = self.ops.set_dns_entry("New.Example.Test.", "192.168.1.9")
        self.assertEqual(result["status"], "preview")
        self.assertEqual(result["requests"], [{"endpoint": "dns_web_app.cgi?add", "form_body": "domainName=new.example.test&domainAddr=192.168.1.9"}])
        self.assertEqual(self.r.posts, [])

    def test_dns_add_verify_and_repeat_is_noop(self):
        result = self.ops.set_dns_entry("new.example.test", "192.168.1.9", confirm=True)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["after"]["domainAddr"], "192.168.1.9")
        self.assertEqual(self.ops.set_dns_entry("NEW.example.test", "192.168.1.9", confirm=True)["status"], "unchanged")
        self.assertEqual(len(self.r.posts), 1)

    def test_dns_replacement_verifies_delete_then_add(self):
        result = self.ops.set_dns_entry("app.example.test", "192.168.1.10", confirm=True)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["before"]["domainAddr"], "192.168.1.9")
        self.assertEqual(result["after"]["domainAddr"], "192.168.1.10")
        self.assertEqual(self.r.posts[0], ("dns_web_app.cgi?act=del&oid=2", None))

    def test_dns_rejected_replacement_restores_old_entry(self):
        self.r.effects = ["apply", "reject", "apply"]
        result = self.ops.set_dns_entry("app.example.test", "192.168.1.10", confirm=True)
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(result["after"]["domainAddr"], "192.168.1.9")

    def test_dns_rejected_restore_reports_failure_with_previous_state(self):
        self.r.effects = ["apply", "reject", "reject"]
        result = self.ops.set_dns_entry("app.example.test", "192.168.1.10", confirm=True)
        self.assertEqual(result["status"], "rollback_failed")
        self.assertEqual(result["before"]["domainAddr"], "192.168.1.9")
        self.assertIsNone(result["after"])
        self.assertEqual(len(self.r.posts), 3)

    def test_failed_verification_reads_report_unknown_without_replay(self):
        baseline = self.r.get("dns_status_web_app.cgi")
        with patch.object(self.r, "get", side_effect=[baseline, requests.Timeout(), requests.Timeout(), requests.Timeout()]):
            result = self.ops.set_dns_entry("new.example.test", "192.168.1.9", confirm=True)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["state_read"])
        self.assertEqual(len(self.r.posts), 1)

    def test_malformed_verification_response_reports_unknown(self):
        baseline = self.r.get("dns_status_web_app.cgi")
        broken = copy.deepcopy(baseline)
        broken["dns_cfg"][0]["domainName"] = None
        with patch.object(self.r, "get", side_effect=[baseline, broken, broken, broken]):
            result = self.ops.set_dns_entry("new.example.test", "192.168.1.9", confirm=True)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(self.r.posts), 1)

    def test_dns_ambiguous_replacement_does_not_retry_or_restore(self):
        self.r.effects = ["apply", "timeout_before"]
        result = self.ops.set_dns_entry("app.example.test", "192.168.1.10", confirm=True)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(self.r.posts), 2)
        self.assertEqual(result["before"]["domainAddr"], "192.168.1.9")

    def test_timeout_after_applied_dns_is_verified_without_replay(self):
        self.r.effects = ["timeout_after"]
        self.assertEqual(self.ops.set_dns_entry("new.example.test", "192.168.1.9", confirm=True)["status"], "applied")
        self.assertEqual(len(self.r.posts), 1)

    def test_success_response_without_state_change_is_unverified(self):
        self.r.effects = ["ignore"]
        self.assertEqual(self.ops.set_dns_entry("new.example.test", "192.168.1.9", confirm=True)["status"], "unverified")

    def test_dns_delete_is_verified_and_idempotent(self):
        self.assertEqual(self.ops.delete_dns_entry("app.example.test", confirm=True)["status"], "applied")
        self.assertEqual(self.ops.delete_dns_entry("app.example.test", confirm=True)["status"], "unchanged")
        self.assertEqual(len(self.r.posts), 1)

    def test_protect_factory_dns_entry(self):
        for action in (lambda: self.ops.delete_dns_entry("webgui.nokiawifi.com", confirm=True),
                       lambda: self.ops.set_dns_entry("webgui.nokiawifi.com", "192.168.1.9", confirm=True)):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(self.r.posts, [])

    def test_invalid_dns_inputs_cannot_write(self):
        for name, ip in [("bad&name.test", "192.168.1.9"), ("*.example.test", "192.168.1.9"),
                         ("-bad.test", "192.168.1.9"), ("ok.test", "::1"), ("ok.test", "224.0.0.1")]:
            with self.subTest(name=name, ip=ip), self.assertRaises(ValueError):
                self.ops.set_dns_entry(name, ip, confirm=True)
        self.assertEqual(self.r.posts, [])

    def test_proxy_disabled_and_malformed_state_fail_closed(self):
        self.r.dns["proxy_cfg"]["Enable"] = 0
        with self.assertRaises(client.RouterError):
            self.ops.set_dns_entry("new.test", "192.168.1.9", confirm=True)
        self.r.dns = {"_raw": "bad response"}
        with self.assertRaises(client.RouterError):
            self.ops.delete_dns_entry("app.example.test", confirm=True)
        self.assertEqual(self.r.posts, [])

    def test_backup_is_unique_and_returns_metadata_only(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.ops.backup_config(directory)
            self.assertEqual(Path(result["path"]).read_bytes(), self.r.export)
            self.assertEqual(result["bytes"], len(self.r.export))
            self.assertNotEqual(self.ops.backup_config(directory)["path"], result["path"])
            self.assertNotIn("content", result)

    def test_bad_export_creates_no_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            for body in (b"", b"<html>Login</html>", b'{"result": 1}'):
                self.r.export = body
                with self.assertRaises(client.RouterError):
                    self.ops.backup_config(directory)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_port_range_add_preview_verify_and_repeat(self):
        kwargs = dict(external_port=45000, internal_ip="192.168.1.9", internal_port=8000, external_end=45002, protocol="TCP/UDP")
        result = self.ops.add_port_forward(**kwargs)
        self.assertEqual(self.r.posts, [])
        form = parse_qs(result["requests"][0]["form_body"])
        self.assertEqual(form["inPortEnd"], ["8002"])
        self.assertEqual(form["wanif"], ["ip,1,1,1"])
        self.assertEqual(self.ops.add_port_forward(**kwargs, confirm=True)["status"], "applied")
        self.assertEqual(self.ops.add_port_forward(**kwargs, confirm=True)["status"], "unchanged")
        self.assertEqual(len(self.r.posts), 1)

    def test_overlap_is_protocol_aware_and_checks_upnp(self):
        self.ops.add_port_forward(45000, "192.168.1.9", 8000, protocol="UDP", confirm=True)
        self.r.maps[0]["X_ASB_COM_Cfg_Source"] = "UPnP"
        self.assertEqual(self.ops.add_port_forward(45000, "192.168.1.9", 8000, protocol="TCP")["status"], "preview")
        for proto in ("UDP", "TCP/UDP"):
            with self.assertRaises(ValueError):
                self.ops.add_port_forward(45000, "192.168.1.10", 9000, protocol=proto, confirm=True)
        self.assertEqual(len(self.r.posts), 1)

    def test_upnp_matching_rule_is_not_a_static_noop(self):
        self.ops.add_port_forward(45000, "192.168.1.9", 8000, confirm=True)
        self.r.maps[0]["X_ASB_COM_Cfg_Source"] = "UPnP"
        with self.assertRaises(ValueError):
            self.ops.add_port_forward(45000, "192.168.1.9", 8000, confirm=True)

    def test_port_delete_uses_full_identity_and_preserves_upnp(self):
        self.ops.add_port_forward(45000, "192.168.1.9", 8000, confirm=True)
        dynamic = {**self.r.maps[0], "ExternalPort": 45001, "X_ASB_COM_ExternalPortEnd": 45001, "X_ASB_COM_Cfg_Source": "UPnP"}
        self.r.maps.append(dynamic)
        result = self.ops.delete_port_forward(45000, "192.168.1.9", 8000, confirm=True)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.r.posts[-1], ("nat_glb_web_app.cgi?v=del_vhost&t=ip&id=1,1,1,9", None))
        self.assertEqual(self.r.maps, [dynamic])
        self.assertEqual(self.ops.delete_port_forward(45000, "192.168.1.9", 8000, confirm=True)["status"], "unchanged")

    def test_cannot_delete_dynamic_rule(self):
        self.ops.add_port_forward(45000, "192.168.1.9", 8000, confirm=True)
        self.r.maps[0]["X_ASB_COM_Cfg_Source"] = "UPnP"
        with self.assertRaises(ValueError):
            self.ops.delete_port_forward(45000, "192.168.1.9", 8000, confirm=True)
        self.assertEqual(len(self.r.posts), 1)

    def test_invalid_forward_inputs_and_targets(self):
        for kwargs in [dict(external_port=0), dict(external_port=65536), dict(external_end=44000),
                       dict(internal_ip="192.168.1.1"), dict(internal_ip="192.168.1.255"),
                       dict(internal_ip="192.168.2.9"), dict(protocol="ICMP"), dict(internal_port=65535, external_end=45002)]:
            args = dict(external_port=45000, internal_ip="192.168.1.9", internal_port=8000)
            args.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.ops.add_port_forward(**args, confirm=True)
        self.assertEqual(self.r.posts, [])

    def test_ambiguous_wan_requires_selection(self):
        conn = copy.deepcopy(self.r.nat["port_mappings"][0]["ipConns"][0])
        conn.update(_oid=2, Name="Internet 2")
        self.r.nat["port_mappings"][0]["ipConns"].append(conn)
        with self.assertRaises(ValueError):
            self.ops.add_port_forward(45000, "192.168.1.9", 8000)
        self.assertEqual(self.ops.add_port_forward(45000, "192.168.1.9", 8000, wan="ip,1,1,1")["status"], "preview")

    def test_port_trigger_overlap_is_rejected(self):
        self.r.nat["port_triggerings"] = [{"did": 1, "_oid": 1, "ipConns": [{"_oid": 1,
            "porttriggerings": [{"OpenPortStart": 44999, "OpenPortEnd": 45001, "OpenProtocol": "TCPorUDP"}]}], "pppConns": []}]
        with self.assertRaises(ValueError):
            self.ops.add_port_forward(45000, "192.168.1.9", 8000, confirm=True)
        self.assertEqual(self.r.posts, [])

    def test_malformed_nat_state_does_not_claim_missing_rule(self):
        self.r.nat = {"port_mappings": []}
        with self.assertRaises(client.RouterError):
            self.ops.delete_port_forward(45000, "192.168.1.9", 8000, confirm=True)
        self.assertEqual(self.r.posts, [])

    def test_delete_can_match_unequal_internal_range_and_remote_host(self):
        self.ops.add_port_forward(45000, "192.168.1.9", 8000, external_end=45002, confirm=True)
        self.r.maps[0]["X_ASB_COM_InternalPortEnd"] = 8000
        self.r.maps[0]["RemoteHost"] = "203.0.113.8"
        result = self.ops.delete_port_forward(45000, "192.168.1.9", 8000, external_end=45002,
                                              internal_end=8000, remote_host="203.0.113.8", confirm=True)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.r.maps, [])

    def test_delete_incomplete_match_reports_mismatch(self):
        self.ops.add_port_forward(45000, "192.168.1.9", 8000, external_end=45002, confirm=True)
        self.r.maps[0]["X_ASB_COM_InternalPortEnd"] = 8000
        with self.assertRaises(ValueError):
            self.ops.delete_port_forward(45000, "192.168.1.9", 8000, external_end=45002, confirm=True)
        self.assertEqual(len(self.r.posts), 1)

    def test_repeated_full_delete_preserves_neighboring_filtered_rule(self):
        self.ops.add_port_forward(45000, "192.168.1.9", 8000, confirm=True)
        self.r.maps[0]["RemoteHost"] = "203.0.113.8"
        neighbor = {**self.r.maps[0], "_oid": 10, "RemoteHost": "203.0.113.9"}
        self.r.maps.append(neighbor)
        args = dict(external_port=45000, internal_ip="192.168.1.9", internal_port=8000,
                    internal_end=8000, remote_host="203.0.113.8", confirm=True)
        self.assertEqual(self.ops.delete_port_forward(**args)["status"], "applied")
        self.assertEqual(self.ops.delete_port_forward(**args)["status"], "unchanged")
        self.assertEqual(self.r.maps, [neighbor])



if __name__ == "__main__":
    unittest.main()
