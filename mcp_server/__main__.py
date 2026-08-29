"""Entry point for `python -m mcp_server`.

Transport modes:
  stdio           — default, for Claude Code local integration
  streamable-http — HTTP mode, for network exposure / Postman testing
  sse             — SSE mode, for legacy MCP clients

Controlled by MCP_TRANSPORT env var, or --transport CLI flag.
Host/port via MCP_HOST / MCP_PORT env vars, or --host / --port CLI flags.
"""

from __future__ import annotations

import argparse
import logging
import os


def main() -> None:
    # Timestamped logs for server-side debugging. FastMCP runs its own uvicorn
    # for HTTP transport, so we can only set the root logger format here; that
    # still dates all application/library log records.
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Multi-domain Knowledge MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default=None,
        help="Transport mode (default: MCP_TRANSPORT env var, or stdio)",
    )
    parser.add_argument("--host", default=None, help="HTTP bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="HTTP bind port (default: 9000)")
    args = parser.parse_args()

    transport = args.transport or os.environ.get("MCP_TRANSPORT", "streamable-http")

    # Set env vars before importing server (FastMCP reads them in constructor)
    if args.host:
        os.environ["MCP_HOST"] = args.host
    if args.port:
        os.environ["MCP_PORT"] = str(args.port)

    from mcp_server.server import mcp

    if transport in ("streamable-http", "http", "sse"):
        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "9000"))
        print(f"MCP Server starting on http://{host}:{port} (transport={transport})")
        # fastmcp 3.x 不再从 MCP_HOST/MCP_PORT env 读参（那是 1.x 构造行为），
        # host/port 必须显式传给 run；transport 统一为 3.x 的 "http"。
        mcp.run(
            transport="http" if transport == "streamable-http" else transport,
            host=host,
            port=port,
        )
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
