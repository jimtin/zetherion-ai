"""Entry point for the updater sidecar."""

import asyncio

from updater_sidecar.server import run_server


def main() -> None:
    """Start the updater sidecar server."""
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
