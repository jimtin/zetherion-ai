"""Updater sidecar for Zetherion AI.

A lightweight service that handles git pull, docker build,
rolling restarts, and health validation for auto-updates.
Runs as a separate container with Docker socket access.
"""

__version__ = "0.1.0"
