import assert from "node:assert/strict";
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  prefersLocalVenvPytest,
  rewriteDirectPytestInvocation,
} from "./run-bounded.mjs";

function withTempRepo(fn) {
  const previousCwd = process.cwd();
  const tempDir = mkdtempSync(path.join(os.tmpdir(), "run-bounded-"));
  process.chdir(tempDir);
  try {
    fn(tempDir);
  } finally {
    process.chdir(previousCwd);
    rmSync(tempDir, { recursive: true, force: true });
  }
}

function writeFakePython(tempDir, scriptBody) {
  const pythonPath =
    process.platform === "win32"
      ? path.join(tempDir, ".venv", "Scripts", "python.cmd")
      : path.join(tempDir, ".venv", "bin", "python");
  mkdirSync(path.dirname(pythonPath), { recursive: true });
  writeFileSync(pythonPath, scriptBody, "utf-8");
  chmodSync(pythonPath, 0o755);
  return pythonPath;
}

test("rewriteDirectPytestInvocation falls back when local venv lacks pytest", () => {
  withTempRepo(() => {
    writeFakePython(
      process.cwd(),
      process.platform === "win32"
        ? "@echo off\r\nif \"%1\"==\"-c\" exit /b 1\r\nexit /b 1\r\n"
        : "#!/bin/sh\nif [ \"$1\" = \"-c\" ]; then exit 1; fi\nexit 1\n",
    );

    const rewritten = rewriteDirectPytestInvocation("python3", ["-m", "pytest", "tests/unit"]);

    assert.equal(rewritten.rewritten, false);
    assert.equal(rewritten.command, "python3");
    assert.deepEqual(rewritten.args, ["-m", "pytest", "tests/unit"]);
  });
});

test("rewriteDirectPytestInvocation prefers local venv when pytest is available", () => {
  withTempRepo(() => {
    const pythonPath = writeFakePython(
      process.cwd(),
      process.platform === "win32"
        ? "@echo off\r\nif \"%1\"==\"-c\" exit /b 0\r\nexit /b 0\r\n"
        : "#!/bin/sh\nif [ \"$1\" = \"-c\" ]; then exit 0; fi\nexit 0\n",
    );

    const rewritten = rewriteDirectPytestInvocation("python3", ["-m", "pytest", "tests/unit"]);

    assert.equal(rewritten.rewritten, true);
    assert.equal(rewritten.command, pythonPath);
    assert.deepEqual(rewritten.args, ["-m", "pytest", "tests/unit"]);
  });
});

test("prefersLocalVenvPytest rewrites heartbeat-wrapped pytest invocations", () => {
  withTempRepo(() => {
    const pythonPath = writeFakePython(
      process.cwd(),
      process.platform === "win32"
        ? "@echo off\r\nif \"%1\"==\"-c\" exit /b 0\r\nexit /b 0\r\n"
        : "#!/bin/sh\nif [ \"$1\" = \"-c\" ]; then exit 0; fi\nexit 0\n",
    );

    const rewritten = prefersLocalVenvPytest(process.execPath, [
      path.resolve("scripts/testing/run-with-heartbeat.mjs"),
      "--heartbeat-seconds",
      "30",
      "--",
      "python3",
      "-m",
      "pytest",
      "tests/unit",
    ]);

    assert.equal(rewritten.rewritten, true);
    const separatorIndex = rewritten.args.indexOf("--");
    assert.notEqual(separatorIndex, -1);
    assert.equal(rewritten.args[separatorIndex + 1], pythonPath);
    assert.deepEqual(rewritten.args.slice(separatorIndex + 2), [
      "-m",
      "pytest",
      "tests/unit",
    ]);
  });
});
