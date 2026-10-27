import importlib
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from tests.test_writes import MemoryRouter
from nokia_router_mcp import server as _server  # Import outside the async event loop.


class ServerToolTests(unittest.IsolatedAsyncioTestCase):
    def test_concurrent_first_calls_share_one_router(self):
        server = self.load()
        server._router = None
        start = threading.Barrier(4)

        def create(config):
            time.sleep(0.05)
            return MemoryRouter()

        def get():
            start.wait()
            return server.router()

        with patch.object(server, "NokiaRouter", side_effect=create), ThreadPoolExecutor(max_workers=4) as pool:
            routers = list(pool.map(lambda _: get(), range(4)))
        self.assertTrue(all(r is routers[0] for r in routers))

    def load(self, writes="0", raw="0"):
        from nokia_router_mcp import server
        with patch.dict(os.environ, {"NOKIA_ROUTER_ALLOW_WRITES": writes, "NOKIA_ROUTER_ALLOW_RAW_WRITES": raw}):
            importlib.reload(server)
        server._router = MemoryRouter()
        return server

    async def test_default_hides_all_write_tools(self):
        server = self.load()
        names = {t.name for t in await server.mcp.list_tools()}
        self.assertTrue({"port_forwards", "dns_config", "port_forward_rules"} <= names)
        self.assertTrue(names.isdisjoint({"set_dns_entry", "delete_dns_entry", "add_port_forward", "delete_port_forward", "backup_config", "post_endpoint", "reboot_router"}))

    async def test_write_tools_are_registered_with_preview_defaults(self):
        server = self.load("1")
        tools = {t.name: t for t in await server.mcp.list_tools()}
        self.assertTrue({"set_dns_entry", "delete_dns_entry", "add_port_forward", "delete_port_forward", "backup_config"} <= tools.keys())
        self.assertNotIn("post_endpoint", tools)
        self.assertFalse(tools["set_dns_entry"].inputSchema["properties"]["confirm"]["default"])
        self.assertFalse(tools["set_dns_entry"].annotations.readOnlyHint)
        self.assertEqual(server.set_dns_entry("new.example.test", "192.168.1.9")["status"], "preview")
        self.assertEqual(server._router.posts, [])

    async def test_raw_write_requires_second_flag_and_validates_before_preview(self):
        server = self.load("1", "1")
        names = {t.name for t in await server.mcp.list_tools()}
        self.assertIn("post_endpoint", names)
        for endpoint in ("../restore_web_app.cgi", "https://example.test/command_web_app.cgi", "RESTORE_web_app.cgi?restore_glb", "usb_web_app.cgi?import", "login_web_app.cgi", "dns_web_app.cgi#restore", "command_no_refresh_web_app.cgi?cat+anything", "service_function_web_app.cgi"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                server.post_endpoint(endpoint)
        self.assertEqual(server.post_endpoint("dns_web_app.cgi?add", "domainName=new.test&domainAddr=192.168.1.9")["status"], "preview")
        self.assertEqual(server._router.posts, [])

    async def test_backup_tool_returns_path_in_configured_directory(self):
        server = self.load("1")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"NOKIA_ROUTER_BACKUP_DIR": directory}):
            result = server.backup_config()
            self.assertTrue(result["path"].startswith(directory))
