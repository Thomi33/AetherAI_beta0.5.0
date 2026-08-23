"""
Smoke test END-TO-END real (sin mocks) contra el server MCP filesystem.

Verifica:
1. MCPClientManager conecta de verdad vía stdio (levanta npx como subproceso).
2. list_tools() devuelve el catálogo real de tools del server.
3. call_tool() con una tool real confirma acceso a la DB runtime de Aether.

Requiere:
- core/config/mcp_servers.json con la entrada "server_filesystem" configurada.
- npx / node instalados y en el PATH.
- paquete `mcp` instalado (pip install "mcp>=1.9,<2" --break-system-packages).

Uso:
    cp test_mcp_e2e_filesystem.py /home/thomi/mi_proyecto_crew/
    cd /home/thomi/mi_proyecto_crew
    python test_mcp_e2e_filesystem.py
"""

import sys

sys.path.insert(0, "/home/thomi/mi_proyecto_crew")

from core.tools.mcp_client import get_mcp_manager, MCPError


SERVER = "server_filesystem"
RUNTIME_DB = "/home/thomi/Aether/db/current.db"


def main():
    print("=" * 70)
    print(f"Conectando a server MCP '{SERVER}' (puede tardar unos segundos")
    print("la primera vez, mientras npx descarga el paquete)...")
    print("=" * 70)

    manager = get_mcp_manager()

    # ── 1. Listar servers configurados ──────────────────────────────
    servers = manager.list_servers()
    print(f"\nServers en config: {servers}")
    if SERVER not in servers:
        print(f"❌ '{SERVER}' no está en mcp_servers.json. Revisá el archivo.")
        return

    # ── 2. Listar tools reales del server (handshake real) ──────────
    try:
        tools = manager.list_tools(SERVER)
    except MCPError as e:
        print(f"\n❌ Falló la conexión/listado: {e}")
        return
    except Exception as e:
        print(f"\n❌ Error inesperado (¿está npx en el PATH? ¿está instalado 'mcp'?): {e}")
        return

    print(f"\n✅ Conectado. Tools disponibles en '{SERVER}':")
    for t in tools:
        print(f"   - {t['name']}: {t['description'][:80]}")

    if not tools:
        print("⚠️  El server conectó pero no devolvió tools. Revisá la versión del paquete.")
        return

    # ── 3. Llamar una tool real ───────────────────────────────────────
    # Preferimos metadata de current.db: SQLite es binario, así que esta es
    # una verificación real de acceso sin intentar mostrarlo como texto.
    nombres_disponibles = [t["name"] for t in tools]
    candidata = next(
        (n for n in ("get_file_info", "list_directory", "list_allowed_directories") if n in nombres_disponibles),
        nombres_disponibles[0],
    )

    if candidata == "get_file_info":
        args = {"path": RUNTIME_DB}
    elif candidata == "list_directory":
        args = {"path": "/home/thomi/Aether/db"}
    else:
        args = {}

    print(f"\nLlamando tool real: '{candidata}' con args={args}...")
    try:
        resultado = manager.call_tool(SERVER, candidata, args)
    except MCPError as e:
        print(f"❌ Falló la llamada: {e}")
        return

    print(f"\n✅ Resultado real recibido ({len(resultado)} caracteres):")
    print(resultado[:1000])

    manager.close()
    print("\n" + "=" * 70)
    print("SMOKE TEST END-TO-END: OK")
    print("=" * 70)


if __name__ == "__main__":
    main()
