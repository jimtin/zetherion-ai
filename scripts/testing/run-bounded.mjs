#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, existsSync, appendFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { LANE_DEFINITIONS, LANE_ORDER, STALL_THRESHOLD_SECONDS } from "./lanes.mjs";

function parseArgs(argv) {
  const opts = {
    lane: "",
    timeoutSeconds: NaN,
    stallSeconds: STALL_THRESHOLD_SECONDS,
    heartbeatSeconds: 30,
    diagnosticsRoot: "artifacts/testing/hang-diagnostics",
    logFile: "docs/migration/test-execution-log.md",
    commandOverride: [],
  };

  let commandStart = -1;
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--") {
      commandStart = i;
      break;
    }
    if (arg === "--lane") {
      opts.lane = String(argv[i + 1] ?? "");
      i += 1;
      continue;
    }
    if (arg === "--timeout-seconds") {
      opts.timeoutSeconds = Number.parseInt(String(argv[i + 1] ?? ""), 10);
      i += 1;
      continue;
    }
    if (arg === "--stall-seconds") {
      opts.stallSeconds = Number.parseInt(String(argv[i + 1] ?? ""), 10);
      i += 1;
      continue;
    }
    if (arg === "--heartbeat-seconds") {
      opts.heartbeatSeconds = Number.parseInt(String(argv[i + 1] ?? ""), 10);
      i += 1;
      continue;
    }
    if (arg === "--diagnostics-root") {
      opts.diagnosticsRoot = String(argv[i + 1] ?? "");
      i += 1;
      continue;
    }
    if (arg === "--log-file") {
      opts.logFile = String(argv[i + 1] ?? "");
      i += 1;
      continue;
    }
  }

  if (commandStart !== -1) {
    opts.commandOverride = argv.slice(commandStart + 1);
  }
  return opts;
}

function validateOptions(opts) {
  if (!opts.lane) {
    throw new Error(`Missing --lane. Allowed lanes: ${LANE_ORDER.join(", ")}`);
  }
  if (!Number.isFinite(opts.stallSeconds) || opts.stallSeconds <= 0) {
    throw new Error("--stall-seconds must be a positive integer");
  }
  if (!Number.isFinite(opts.heartbeatSeconds) || opts.heartbeatSeconds <= 0) {
    throw new Error("--heartbeat-seconds must be a positive integer");
  }
}

function laneCommand(opts) {
  const lane = LANE_DEFINITIONS[opts.lane];
  if (!lane) {
    throw new Error(`Unknown lane "${opts.lane}". Allowed lanes: ${LANE_ORDER.join(", ")}`);
  }
  if (lane.unavailable) {
    throw new Error(`Lane "${opts.lane}" is unavailable in this repository: ${lane.description}`);
  }

  const timeoutSeconds = Number.isFinite(opts.timeoutSeconds)
    ? opts.timeoutSeconds
    : lane.timeoutSeconds ?? 1800;

  const rawCommand = opts.commandOverride.length > 0 ? opts.commandOverride : lane.command;
  if (!Array.isArray(rawCommand) || rawCommand.length === 0) {
    throw new Error(`Lane "${opts.lane}" has no executable command`);
  }

  if (lane.heartbeat) {
    return {
      timeoutSeconds,
      command: [
        process.execPath,
        path.resolve("scripts/testing/run-with-heartbeat.mjs"),
        "--heartbeat-seconds",
        String(opts.heartbeatSeconds),
        "--",
        ...rawCommand,
      ],
    };
  }

  return {
    timeoutSeconds,
    command: rawCommand,
  };
}

function nowIso() {
  return new Date().toISOString();
}

function ensureLogFile(filePath) {
  const absPath = path.resolve(filePath);
  mkdirSync(path.dirname(absPath), { recursive: true });
  if (!existsSync(absPath)) {
    const initial = [
      "# Test Execution Log",
      "",
      "Append-only lane execution ledger.",
      "",
      "| Timestamp (UTC) | Lane | Command | Result | Duration (s) | Reason | Diagnostics |",
      "|---|---|---|---|---:|---|---|",
      "",
    ].join("\n");
    writeFileSync(absPath, initial, "utf-8");
  }
  return absPath;
}

function appendLogRow(filePath, row) {
  const safe = (value) => String(value ?? "").replace(/\|/g, "\\|");
  const line = `| ${safe(row.timestamp)} | ${safe(row.lane)} | \`${safe(
    row.command,
  )}\` | ${safe(row.result)} | ${safe(row.durationSeconds)} | ${safe(row.reason)} | ${safe(
    row.diagnostics,
  )} |\n`;
  appendFileSync(filePath, line, "utf-8");
}

function killProcessTree(pid) {
  try {
    process.kill(-pid, "SIGTERM");
  } catch {
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      // noop
    }
  }

  const killer = spawnSync("bash", ["-lc", `sleep 2; kill -KILL -${pid} >/dev/null 2>&1 || true`], {
    detached: false,
  });
  return killer.status ?? 0;
}

function captureDiagnostics({ lane, reason, diagnosticsRoot, pid }) {
  const stamp = nowIso().replaceAll(":", "-");
  const outDir = path.resolve(diagnosticsRoot, `${lane}-${stamp}`);
  mkdirSync(outDir, { recursive: true });

  const args = [
    path.resolve("scripts/testing/test-hang-diagnostics.mjs"),
    "--output-dir",
    outDir,
    "--lane",
    lane,
    "--reason",
    reason,
  ];
  if (pid) {
    args.push("--pid", String(pid));
  }

  const result = spawnSync(process.execPath, args, {
    cwd: process.cwd(),
    env: process.env,
    encoding: "utf-8",
  });

  if (result.status !== 0) {
    const failurePath = path.join(outDir, "diagnostics-error.txt");
    writeFileSync(
      failurePath,
      `${result.stdout ?? ""}\n${result.stderr ?? ""}`.trim() + "\n",
      "utf-8",
    );
  }
  const rel = path.relative(process.cwd(), outDir) || outDir;
  return rel.startsWith(".") ? rel : `./${rel}`;
}

async function run() {
  const opts = parseArgs(process.argv.slice(2));
  validateOptions(opts);
  const logFile = ensureLogFile(opts.logFile);

  const lane = laneCommand(opts);
  const [command, ...args] = lane.command;
  const commandDisplay = lane.command.join(" ");
  const startedAtMs = Date.now();
  let lastOutputAtMs = Date.now();
  let terminatedReason = "";
  let diagnosticsPath = "";
  let watchdogSettled = false;

  const child = spawn(command, args, {
    cwd: process.cwd(),
    env: process.env,
    stdio: ["ignore", "pipe", "pipe"],
    detached: true,
  });

  const pipeOutput = (stream, writer) => {
    stream.on("data", (chunk) => {
      lastOutputAtMs = Date.now();
      writer.write(chunk);
    });
  };

  if (child.stdout) {
    pipeOutput(child.stdout, process.stdout);
  }
  if (child.stderr) {
    pipeOutput(child.stderr, process.stderr);
  }

  const watchdog = setInterval(() => {
    if (watchdogSettled) {
      return;
    }
    const nowMs = Date.now();
    const elapsedSeconds = Math.floor((nowMs - startedAtMs) / 1000);
    const silenceSeconds = Math.floor((nowMs - lastOutputAtMs) / 1000);

    if (elapsedSeconds >= lane.timeoutSeconds) {
      terminatedReason = `timeout>${lane.timeoutSeconds}s`;
    } else if (silenceSeconds >= opts.stallSeconds) {
      terminatedReason = `stall>${opts.stallSeconds}s`;
    } else {
      return;
    }

    watchdogSettled = true;
    killProcessTree(child.pid);
    diagnosticsPath = captureDiagnostics({
      lane: opts.lane,
      reason: terminatedReason,
      diagnosticsRoot: opts.diagnosticsRoot,
      pid: child.pid,
    });
  }, 1000);

  const status = await new Promise((resolve) => {
    child.on("exit", (code, signal) => {
      clearInterval(watchdog);
      const durationSeconds = Math.floor((Date.now() - startedAtMs) / 1000);
      if (terminatedReason) {
        resolve({
          code: 124,
          result: terminatedReason.startsWith("stall") ? "stalled" : "timed_out",
          reason: terminatedReason,
          durationSeconds,
          signal,
        });
        return;
      }

      if (typeof code === "number" && code === 0) {
        resolve({
          code: 0,
          result: "passed",
          reason: "",
          durationSeconds,
          signal,
        });
        return;
      }

      resolve({
        code: typeof code === "number" ? code : 1,
        result: "failed",
        reason: signal ? `signal:${signal}` : `exit:${code}`,
        durationSeconds,
        signal,
      });
    });
    child.on("error", (error) => {
      clearInterval(watchdog);
      resolve({
        code: 1,
        result: "failed",
        reason: `spawn_error:${error.message}`,
        durationSeconds: Math.floor((Date.now() - startedAtMs) / 1000),
        signal: "",
      });
    });
  });

  appendLogRow(logFile, {
    timestamp: nowIso(),
    lane: opts.lane,
    command: commandDisplay,
    result: status.result,
    durationSeconds: status.durationSeconds,
    reason: status.reason,
    diagnostics: diagnosticsPath || "-",
  });

  process.exit(status.code);
}

run().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(1);
});
