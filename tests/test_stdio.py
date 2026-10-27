"""Exercise the public stdio entry point without connecting to a router.

Also run directly from outside the source checkout to smoke-test an installed wheel.
"""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class StdioTests(unittest.IsolatedAsyncioTestCase):
    async def discover(self, writes, raw):
        with tempfile.TemporaryDirectory() as directory:
            params = StdioServerParameters(
                command=str(Path(sys.executable).with_name("nokia-router-mcp.exe" if os.name == "nt" else "nokia-router-mcp")),
                args=[],
                cwd=directory,
                env={
                    "NOKIA_ROUTER_URL": "http://127.0.0.1:1",
                    "NOKIA_ROUTER_USERNAME": "synthetic",
                    "NOKIA_ROUTER_PASSWORD": "synthetic",
                    "NOKIA_ROUTER_ALLOW_WRITES": writes,
                    "NOKIA_ROUTER_ALLOW_RAW_WRITES": raw,
                },
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    return {tool.name: tool for tool in result.tools}

    async def test_default_stdio_has_no_mutation_tools(self):
        tools = await asyncio.wait_for(self.discover("0", "0"), 30)
        self.assertIn("dns_config", tools)
        self.assertIn("port_forward_rules", tools)
        self.assertNotIn("set_dns_entry", tools)
        self.assertNotIn("post_endpoint", tools)
        self.assertEqual(len(tools), 15)

    async def test_enabled_stdio_exposes_structured_writes_with_preview(self):
        tools = await asyncio.wait_for(self.discover("1", "0"), 30)
        for name in ("set_dns_entry", "delete_dns_entry", "add_port_forward", "delete_port_forward"):
            self.assertIn(name, tools)
            self.assertFalse(tools[name].inputSchema["properties"]["confirm"]["default"])
        self.assertIn("backup_config", tools)
        self.assertNotIn("post_endpoint", tools)
        self.assertEqual(len(tools), 21)

    async def test_raw_stdio_requires_both_flags(self):
        tools = await asyncio.wait_for(self.discover("0", "1"), 30)
        self.assertNotIn("post_endpoint", tools)
        tools = await asyncio.wait_for(self.discover("1", "1"), 30)
        self.assertIn("post_endpoint", tools)
        self.assertEqual(len(tools), 22)


if __name__ == "__main__":
    unittest.main()
