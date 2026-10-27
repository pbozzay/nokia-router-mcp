"""Check release archives exclude local operating files and contain the write modules."""
from pathlib import Path, PurePosixPath
import tarfile
import zipfile


def check(names):
    for name in names:
        parts = PurePosixPath(name).parts
        if any(part in {".env", ".git", ".venv", "backups", "probe-output", "__pycache__"}
               or (part.startswith(".env.") and part != ".env.example") for part in parts):
            raise SystemExit(f"Private/local path in distribution: {name}")
        if name.endswith((".cfg", ".pyc", ".pem", ".key")):
            raise SystemExit(f"Unexpected sensitive or generated file: {name}")
    for module in ("operations.py", "port_forwarding.py", "write_support.py", "server.py"):
        if not any(name.endswith("nokia_router_mcp/" + module) for name in names):
            raise SystemExit(f"Missing write implementation: {module}")


archives = list(Path("dist").glob("*.whl")) + list(Path("dist").glob("*.tar.gz"))
if len(archives) != 2:
    raise SystemExit("Expected one wheel and one source distribution in dist/.")
for archive in archives:
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as opened:
            check(opened.namelist())
    else:
        with tarfile.open(archive) as opened:
            check(opened.getnames())
    print(f"Checked {archive.name}: write modules present, local operating files excluded")
