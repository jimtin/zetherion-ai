#!/usr/bin/env python3
"""Auto-increment SemVer patch release for deployed main SHAs."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SEMVER_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> SemVer | None:
        match = SEMVER_RE.match(value.strip())
        if not match:
            return None
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    def bump_patch(self) -> SemVer:
        return SemVer(self.major, self.minor, self.patch + 1)

    def tag(self) -> str:
        return f"v{self.major}.{self.minor}.{self.patch}"


def _is_enabled(raw: str | None) -> bool:
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_sha(value: str) -> str:
    return value.strip().lower()


def _write_receipt(path: str, payload: dict[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _gh_request(
    token: str,
    repo: str,
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
) -> dict[str, object] | list[dict[str, object]]:
    url = f"https://api.github.com/repos/{repo}{path}"
    data: bytes | None = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(url, data=data, method=method.upper())
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "zetherion-release-auto-increment")
    if payload is not None:
        request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API request failed ({method} {path}): HTTP {exc.code}: {detail}"
        ) from exc

    if not raw:
        return {}
    parsed = json.loads(raw)
    if isinstance(parsed, dict | list):
        return parsed
    raise RuntimeError("GitHub API returned non-JSON payload")


def _find_existing_release_for_sha(
    releases: list[dict[str, object]],
    sha: str,
) -> dict[str, object] | None:
    normalized = _normalize_sha(sha)
    for release in releases:
        tag_name = str(release.get("tag_name", ""))
        if not SEMVER_RE.match(tag_name):
            continue
        target = _normalize_sha(str(release.get("target_commitish", "")))
        if not target:
            continue
        if target == normalized or target.startswith(normalized) or normalized.startswith(target):
            return release
    return None


def _latest_semver_release(releases: list[dict[str, object]]) -> tuple[SemVer, str] | None:
    best: tuple[SemVer, str] | None = None
    for release in releases:
        if bool(release.get("draft", False)) or bool(release.get("prerelease", False)):
            continue
        tag_name = str(release.get("tag_name", ""))
        parsed = SemVer.parse(tag_name)
        if parsed is None:
            continue
        if best is None or (parsed.major, parsed.minor, parsed.patch) > (
            best[0].major,
            best[0].minor,
            best[0].patch,
        ):
            best = (parsed, tag_name)
    return best


def _build_release_notes(repo: str, token: str, previous_tag: str | None, sha: str) -> str:
    header = "## Automated Main Deployment Release\n\n"
    header += f"Deployed commit: `{sha}`\n"

    if not previous_tag:
        header += "\nInitial automated release for deployed main.\n"
        return header

    compare_path = f"/compare/{urllib.parse.quote(previous_tag)}...{urllib.parse.quote(sha)}"
    compare = _gh_request(token, repo, "GET", compare_path)
    if not isinstance(compare, dict):
        return header

    commits = compare.get("commits")
    lines: list[str] = [header, "\n### Changes\n"]
    if isinstance(commits, list) and commits:
        for commit in commits[:100]:
            if not isinstance(commit, dict):
                continue
            sha_short = str(commit.get("sha", ""))[:7]
            msg_obj = commit.get("commit")
            message = ""
            if isinstance(msg_obj, dict):
                message = str(msg_obj.get("message", "")).splitlines()[0]
            if not message:
                message = "(no message)"
            lines.append(f"- {sha_short} {message}")
    else:
        lines.append("- No commit delta available from compare API.")

    lines.append("")
    lines.append(f"Full compare: https://github.com/{repo}/compare/{previous_tag}...{sha}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True, help="Deployed commit SHA")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument(
        "--output",
        default="release-receipt.json",
        help="Path for JSON receipt output",
    )
    args = parser.parse_args()

    if not args.repo:
        print("ERROR: --repo is required (or set GITHUB_REPOSITORY)")
        return 1

    enabled = _is_enabled(os.environ.get("RELEASE_AUTO_INCREMENT_ENABLED"))
    if not enabled:
        receipt = {
            "status": "skipped_disabled",
            "sha": args.sha,
            "repo": args.repo,
        }
        _write_receipt(args.output, receipt)
        print(json.dumps(receipt))
        return 0

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("ERROR: GITHUB_TOKEN or GH_TOKEN is required")
        return 1

    sha = _normalize_sha(args.sha)
    if not sha:
        print("ERROR: --sha cannot be empty")
        return 1

    try:
        releases_payload = _gh_request(token, args.repo, "GET", "/releases?per_page=100")
        if not isinstance(releases_payload, list):
            raise RuntimeError("GitHub releases endpoint did not return a list")
        releases = [r for r in releases_payload if isinstance(r, dict)]

        existing = _find_existing_release_for_sha(releases, sha)
        if existing is not None:
            receipt = {
                "status": "skipped_existing",
                "sha": sha,
                "repo": args.repo,
                "tag": str(existing.get("tag_name", "")),
                "release_id": existing.get("id"),
                "html_url": existing.get("html_url"),
            }
            _write_receipt(args.output, receipt)
            print(json.dumps(receipt))
            return 0

        latest = _latest_semver_release(releases)
        if latest is None:
            next_version = SemVer(0, 1, 0)
            previous_tag = None
        else:
            next_version = latest[0].bump_patch()
            previous_tag = latest[1]

        next_tag = next_version.tag()
        notes = _build_release_notes(args.repo, token, previous_tag, sha)

        created = _gh_request(
            token,
            args.repo,
            "POST",
            "/releases",
            payload={
                "tag_name": next_tag,
                "target_commitish": sha,
                "name": next_tag,
                "body": notes,
                "draft": False,
                "prerelease": False,
                "generate_release_notes": False,
            },
        )
        if not isinstance(created, dict):
            raise RuntimeError("GitHub release creation returned an unexpected payload")

        receipt = {
            "status": "created",
            "sha": sha,
            "repo": args.repo,
            "tag": next_tag,
            "release_id": created.get("id"),
            "html_url": created.get("html_url"),
            "previous_tag": previous_tag,
        }
        _write_receipt(args.output, receipt)
        print(json.dumps(receipt))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
