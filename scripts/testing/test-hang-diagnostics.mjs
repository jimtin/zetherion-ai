#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

function parseArgs(argv) {
  const out = {
    outputDir: "",
    pid: "",
    lane: "",
    reason: "",
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--output-dir") {
      out.outputDir = String(argv[i + 1] ?? "");
      i += 1;
      continue;
    }
    if (arg === "--pid") {
      out.pid = String(argv[i + 1] ?? "");
      i += 1;
      continue;
    }
    if (arg === "--lane") {
      out.lane = String(argv[i + 1] ?? "");
      i += 1;
      continue;
    }
    if (arg === "--reason") {
      out.reason = String(argv[i + 1] ?? "");
      i += 1;
      continue;
    }
  }

  if (!out.outputDir) {
    throw new Error("Missing required --output-dir");
  }
  return out;
}

function runCapture(filePath, cmd, args, timeoutMs = 20000) {
  const result = spawnSync(cmd, args, {
    cwd: process.cwd(),
    env: process.env,
    encoding: "utf-8",
    timeout: timeoutMs,
  });

  const stdout = result.stdout ?? "";
  const stderr = result.stderr ?? "";
  const content = `${stdout}${stderr ? `\n[stderr]\n${stderr}` : ""}`;
  writeFileSync(filePath, content, "utf-8");
}

function commandExists(cmd) {
  const check = spawnSync("bash", ["-lc", `command -v ${cmd}`], {
    cwd: process.cwd(),
    env: process.env,
  });
  return check.status === 0;
}

function maybeCaptureSample(outputDir, pid) {
  if (!pid || process.platform !== "darwin") {
    return;
  }
  if (!commandExists("sample")) {
    return;
  }

  const samplePath = path.join(outputDir, "sample.txt");
  spawnSync("sample", [pid, "5", "-file", samplePath], {
    cwd: process.cwd(),
    env: process.env,
    timeout: 15000,
  });
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  mkdirSync(args.outputDir, { recursive: true });

  const meta = {
    captured_at_utc: new Date().toISOString(),
    lane: args.lane,
    reason: args.reason,
    pid: args.pid,
    platform: process.platform,
    node: process.version,
    cwd: process.cwd(),
  };
  writeFileSync(path.join(args.outputDir, "metadata.json"), `${JSON.stringify(meta, null, 2)}\n`);

  runCapture(
    path.join(args.outputDir, "ps.txt"),
    "ps",
    ["-ax", "-o", "pid,ppid,pgid,stat,etime,command"],
  );

  if (args.pid) {
    runCapture(path.join(args.outputDir, "lsof.txt"), "lsof", ["-nP", "-p", args.pid]);
  } else {
    runCapture(path.join(args.outputDir, "lsof.txt"), "lsof", ["-nP"]);
  }

  maybeCaptureSample(args.outputDir, args.pid);
  process.stdout.write(`${args.outputDir}\n`);
}

try {
  main();
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(1);
}

