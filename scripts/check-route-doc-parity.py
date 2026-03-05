#!/usr/bin/env python3
"""Check route coverage parity between server registrations and docs references."""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SKILLS_SERVER = REPO_ROOT / "src/zetherion_ai/skills/server.py"
PUBLIC_SERVER = REPO_ROOT / "src/zetherion_ai/api/server.py"
YOUTUBE_ROUTES = REPO_ROOT / "src/zetherion_ai/api/routes/youtube.py"

SKILLS_DOC = REPO_ROOT / "docs/technical/api-reference.md"
PUBLIC_DOC = REPO_ROOT / "docs/technical/public-api-reference.md"

METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
LITERAL_ROUTE_RE = re.compile(
    r"app\.router\.add_(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]"
)
DOC_ENDPOINT_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s`)<]+)")


def normalize_path(path: str) -> str:
    cleaned = path.strip().split("?", 1)[0]
    if cleaned != "/":
        cleaned = cleaned.rstrip("/")
    return cleaned


def _eval_string(expr: ast.AST, env: dict[str, str]) -> str | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.Name):
        return env.get(expr.id)
    if isinstance(expr, ast.JoinedStr):
        parts: list[str] = []
        for value in expr.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if isinstance(value, ast.FormattedValue):
                resolved = _eval_string(value.value, env)
                if resolved is None:
                    return None
                parts.append(resolved)
                continue
            return None
        return "".join(parts)
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

    route = _eval_string(call.args[0], env)
    if route is None:
        return None

    return method, normalize_path(route)


def extract_literal_routes(path: Path) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    text = path.read_text(encoding="utf-8")
    for match in LITERAL_ROUTE_RE.finditer(text):
        method = match.group(1).upper()
        route = normalize_path(match.group(2))
        routes.add((method, route))
    return routes


def extract_worker_routes(path: Path) -> set[tuple[str, str]]:
    source = path.read_text(encoding="utf-8")
    module = ast.parse(source)
    routes: set[tuple[str, str]] = set()

    for node in ast.walk(module):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        env: dict[str, str] = {}
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                resolved = _eval_string(stmt.value, env)
                if resolved is not None:
                    env[stmt.targets[0].id] = resolved
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                endpoint = _extract_route_from_call(stmt.value, env)
                if endpoint is not None and "/workers/" in endpoint[1]:
                    routes.add(endpoint)

    return routes


def extract_youtube_routes(path: Path) -> set[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    module = ast.parse(text)

    target_fn: ast.FunctionDef | None = None
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == "register_youtube_routes":
            target_fn = node
            break

    if target_fn is None:
        raise RuntimeError("register_youtube_routes() not found")

    env: dict[str, str] = {}
    routes: set[tuple[str, str]] = set()

    for stmt in target_fn.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
        ):
            key = stmt.targets[0].id
            value = _eval_string(stmt.value, env)
            if value is not None:
                env[key] = value
            continue

        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            endpoint = _extract_route_from_call(stmt.value, env)
            if endpoint is not None:
                routes.add(endpoint)

    return routes


def extract_doc_endpoints(path: Path) -> set[tuple[str, str]]:
    endpoints: set[tuple[str, str]] = set()
    text = path.read_text(encoding="utf-8")
    in_code_block = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        # Endpoints are intentionally listed in headings/bullets.
        # Avoid parsing raw HTTP snippets.
        if in_code_block:
            continue

        for match in DOC_ENDPOINT_RE.finditer(line):
            method = match.group(1)
            route = normalize_path(match.group(2).rstrip(".,"))
            endpoints.add((method, route))

    return endpoints


def print_diff(label: str, expected: set[tuple[str, str]], documented: set[tuple[str, str]]) -> int:
    missing = sorted(expected - documented)
    extra = sorted(documented - expected)

    if not missing and not extra:
        print(f"{label}: parity check passed ({len(expected)} routes).")
        return 0

    print(f"{label}: parity check failed.")
    if missing:
        print("  Missing from docs:")
        for method, route in missing:
            print(f"    - {method} {route}")
    if extra:
        print("  Present in docs but not registered:")
        for method, route in extra:
            print(f"    - {method} {route}")
    return 1


def main() -> int:
    skills_routes = extract_literal_routes(SKILLS_SERVER) | extract_worker_routes(SKILLS_SERVER)
    public_routes = extract_literal_routes(PUBLIC_SERVER) | extract_youtube_routes(YOUTUBE_ROUTES)

    skills_doc_routes = extract_doc_endpoints(SKILLS_DOC)
    public_doc_routes = extract_doc_endpoints(PUBLIC_DOC)

    failures = 0
    failures += print_diff("Skills API", skills_routes, skills_doc_routes)
    failures += print_diff("Public API", public_routes, public_doc_routes)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
