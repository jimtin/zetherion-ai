"""Auto-update pipeline for Zetherion AI deployments.

Detects new GitHub releases, manages the update lifecycle
(pull → build → restart → validate), and supports rollback
on health check failure.
"""
