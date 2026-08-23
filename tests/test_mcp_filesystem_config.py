#!/usr/bin/env python3
"""Contrato de permisos del servidor MCP filesystem."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_filesystem_mcp_expone_raiz_y_db_runtime_existe():
    config = json.loads((ROOT / "core/config/mcp_servers.json").read_text())
    filesystem = config["server_filesystem"]
    assert filesystem["args"][-1] == "/"
    assert Path("/home/thomi/Aether/db/current.db").is_file()


if __name__ == "__main__":
    test_filesystem_mcp_expone_raiz_y_db_runtime_existe()
    print("✅ test_filesystem_mcp_expone_raiz_y_db_runtime_existe")
