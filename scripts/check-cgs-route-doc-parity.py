#!/usr/bin/env python3
"""Check parity between CGS gateway routes and OpenAPI documentation."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CGS_ROUTE_FILES = [
    REPO_ROOT / "src/zetherion_ai/cgs_gateway/server.py",
    REPO_ROOT / "src/zetherion_ai/cgs_gateway/routes/runtime.py",
    REPO_ROOT / "src/zetherion_ai/cgs_gateway/routes/internal.py",
    REPO_ROOT / "src/zetherion_ai/cgs_gateway/routes/internal_admin.py",
    REPO_ROOT / "src/zetherion_ai/cgs_gateway/routes/reporting.py",
]
OPENAPI_FILE = REPO_ROOT / "docs/technical/openapi-cgs-gateway.yaml"
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def normalize_path(path: str) -> str:
    value = path.strip().split("?", 1)[0]
    if value != "/":
        value = value.rstrip("/")
    return value


def _eval_string(expr: ast.AST, env: dict[str, str]) -> str | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.Name):
        return env.get(expr.id)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left = _eval_string(expr.left, env)
        right = _eval_string(expr.right, env)
        if left is None or right is None:
            return None
        return left + right
    return None


def _extract_route_from_call(call: ast.Call, env: dict[str, str]) -> tuple[str, str] | None:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    method_name = func.attr
    if not method_name.startswith("add_"):
        return None
    method = method_name.removeprefix("add_").upper()
    if method not in METHODS:
        return None

    router_obj = func.value
    if not (
        isinstance(router_obj, ast.Attribute)
        and router_obj.attr == "router"
        and isinstance(router_obj.value, ast.Name)
        and router_obj.value.id == "app"
    ):
        return None

    if not call.args:
        return None
    path = _eval_string(call.args[0], env)
    if path is None:
        return None
    return method, normalize_path(path)


def extract_cgs_routes(path: Path) -> set[tuple[str, str]]:
    source = path.read_text(encoding="utf-8")
    module = ast.parse(source)
    routes: set[tuple[str, str]] = set()

    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        env: dict[str, str] = {}
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                value = _eval_string(stmt.value, env)
                if value is not None:
                    env[stmt.targets[0].id] = value
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                route = _extract_route_from_call(stmt.value, env)
                if route is not None:
                    routes.add(route)
    return {route for route in routes if route[1].startswith("/service/ai/v1")}


def extract_openapi_routes(path: Path) -> set[tuple[str, str]]:
    content = path.read_text(encoding="utf-8")
    routes: set[tuple[str, str]] = set()
    current_path: str | None = None
    in_paths = False

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if line == "paths:":
            in_paths = True
            continue
        if in_paths and line and not line.startswith("  "):
            break
        if not in_paths:
            continue

        if line.startswith("  /") and line.endswith(":"):
            current_path = normalize_path(line[2:-1].strip())
            continue
        if current_path is None:
            continue

        stripped = line.strip()
        if not stripped.endswith(":"):
            continue
        method = stripped[:-1].upper()
        if method in METHODS:
            routes.add((method, current_path))
    return routes


def main() -> int:
    registered: set[tuple[str, str]] = set()
    for file in CGS_ROUTE_FILES:
        registered |= extract_cgs_routes(file)

    documented = extract_openapi_routes(OPENAPI_FILE)
    missing = sorted(registered - documented)
    extra = sorted(documented - registered)

    if not missing and not extra:
        print(f"CGS route-doc parity check passed ({len(registered)} routes).")
        return 0

    print("CGS route-doc parity check failed.")
    if missing:
        print("Missing from OpenAPI:")
        for method, route in missing:
            print(f"  - {method} {route}")
    if extra:
        print("Present in OpenAPI but not registered:")
        for method, route in extra:
            print(f"  - {method} {route}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
