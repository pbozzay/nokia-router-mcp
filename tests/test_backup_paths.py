import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nokia_router_mcp import cli, server
from tests.test_writes import MemoryRouter


class BackupPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.router = MemoryRouter()
        self.router.logout = lambda: None
        self.env = patch.dict(os.environ, {"NOKIA_ROUTER_ALLOW_WRITES": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        importlib.reload(server)
        os.environ.pop("NOKIA_ROUTER_BACKUP_DIR", None)
        server._router = self.router
        self.home = patch.object(Path, "home", return_value=self.root / "home")
        self.home.start()
        self.addCleanup(self.home.stop)
        # Model an installed package without writing into the real installation.
        for module in (server, cli):
            location = self.root / "installation" / "lib" / "package" / "module.py"
            replacement = patch.object(module, "__file__", str(location))
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_mcp_default_export_goes_to_user_storage(self):
        result = server.backup_config()
        path = Path(result["path"])
        self.assertEqual(path.parent, self.root / "home" / ".nokia-router-mcp" / "backups")
        self.assertGreater(path.stat().st_size, 0)

    def run_cli(self, *args):
        output = io.StringIO()
        with patch.object(cli.sys, "argv", ["nokia-router-mcp", "backup", *args]), contextlib.redirect_stdout(output):
            cli.main()
        return Path(json.loads(output.getvalue())["path"])

    def test_cli_uses_configured_backup_directory(self):
        destination = self.root / "configured"
        os.environ["NOKIA_ROUTER_BACKUP_DIR"] = str(destination)
        path = self.run_cli()
        self.assertEqual(path.parent, destination)
        self.assertGreater(path.stat().st_size, 0)

    def test_cli_explicit_destination_overrides_environment(self):
        os.environ["NOKIA_ROUTER_BACKUP_DIR"] = str(self.root / "configured")
        path = self.run_cli(str(self.root / "explicit"))
        self.assertEqual(path.parent, self.root / "explicit")
        self.assertFalse((self.root / "configured").exists())
