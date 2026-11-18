"""List tools from `openapi2mcp serve <spec>` over stdio (proves serve mode, no generated files).

Usage: uv run python examples/verify_serve.py <spec-url-or-path> [extra serve args]
"""

import asyncio
import sys

from mcp import Client, StdioServerParameters


async def main(spec: str, extra: list[str]) -> None:
    params = StdioServerParameters(command="uv", args=["run", "--quiet", "openapi2mcp", "serve", spec, *extra])
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        print(f"{len(tools)} tools from serve mode:")
        for t in tools:
            print(f"  - {t.name}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2:]))
