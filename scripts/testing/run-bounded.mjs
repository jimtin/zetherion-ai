#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, existsSync, appendFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

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

function isReadableFile(filePath) {
  return typeof filePath === "string" && filePath.trim() !== "" && existsSync(filePath);
}

function candidatePythonExecutables(command, args) {
  const values = [command, ...args].map((value) => String(value ?? "").trim());
  const matches = values.filter((value) => /(^|\/)python([0-9]+(\.[0-9]+)*)?$/.test(value));
  const deduped = [];
  for (const executable of [...matches, "python3"]) {
    if (executable && !deduped.includes(executable)) {
      deduped.push(executable);
    }
  }
  return deduped;
}

function resolvePythonCaBundle(command, args, env) {
  const inherited = String(env.SSL_CERT_FILE ?? "").trim();
  if (isReadableFile(inherited)) {
    return inherited;
  }

  const preferredPaths = ["/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"];
  for (const candidate of preferredPaths) {
    if (isReadableFile(candidate)) {
      return candidate;
    }
  }

  const probeScript = "import certifi; print(certifi.where())";
  for (const executable of candidatePythonExecutables(command, args)) {
    const result = spawnSync(executable, ["-c", probeScript], {
      cwd: process.cwd(),
      env,
      encoding: "utf-8",
    });
    if (result.status !== 0) {
      continue;
    }
    const candidate = String(result.stdout ?? "").trim();
    if (isReadableFile(candidate)) {
      return candidate;
    }
  }

  return inherited;
}

function isPytestInvocation(command, args) {
  const values = [command, ...args].map((value) => String(value ?? "").trim());
  if (values.some((value) => /(^|\/)pytest$/.test(value))) {
    return true;
  }
  for (let i = 0; i < values.length - 1; i += 1) {
    if (values[i] === "-m" && values[i + 1] === "pytest") {
      return true;
    }
  }
  return false;
}

function isPythonExecutable(command) {
  const value = String(command ?? "").trim();
  return /(^|\/)python([0-9]+(\.[0-9]+)*)?$/.test(value);
}

function localVenvPythonCandidates() {
  return [
    path.resolve(".venv/bin/python"),
    path.resolve(".venv/Scripts/python.exe"),
    path.resolve(".venv/Scripts/python.cmd"),
    path.resolve("venv/bin/python"),
    path.resolve("venv/Scripts/python.exe"),
    path.resolve("venv/Scripts/python.cmd"),
  ];
}

function pythonExecutableSupportsPytest(executable, env = process.env) {
  const isWindowsCmdWrapper =
    process.platform === "win32" && /\.(cmd|bat)$/i.test(String(executable ?? "").trim());
  const result = isWindowsCmdWrapper
    ? spawnSync("cmd.exe", ["/d", "/s", "/c", executable, "-c", "import pytest"], {
        cwd: process.cwd(),
        env,
        encoding: "utf-8",
      })
    : spawnSync(executable, ["-c", "import pytest"], {
        cwd: process.cwd(),
        env,
        encoding: "utf-8",
      });
  return result.status === 0;
}

function isHeartbeatWrapper(command, args) {
  const value = String(command ?? "").trim();
  if (!/(^|\/)(node|node[0-9]+)?$/.test(path.basename(value)) && value !== process.execPath) {
    return false;
  }
  if (args.length === 0) {
    return false;
  }
  const wrapperPath = String(args[0] ?? "")
    .trim()
    .replaceAll("\\", "/");
  return wrapperPath.endsWith("scripts/testing/run-with-heartbeat.mjs");
}

function rewriteDirectPytestInvocation(command, args, env = process.env) {
  if (process.env.CI) {
    return { command, args, rewritten: false };
  }
  if (!isPytestInvocation(command, args)) {
    return { command, args, rewritten: false };
  }

  let venvPython = "";
  for (const candidate of localVenvPythonCandidates()) {
    if (!isReadableFile(candidate)) {
      continue;
    }
    if (!pythonExecutableSupportsPytest(candidate, env)) {
      continue;
    }
    venvPython = candidate;
    break;
  }
  if (!venvPython) {
    return { command, args, rewritten: false };
  }

  if (isPythonExecutable(command)) {
    return { command: venvPython, args, rewritten: true };
  }

  if (/(^|\/)pytest$/.test(String(command ?? "").trim())) {
    return {
      command: venvPython,
      args: ["-m", "pytest", ...args],
      rewritten: true,
    };
  }

  return { command, args, rewritten: false };
}

function prefersLocalVenvPytest(command, args) {
  const directRewrite = rewriteDirectPytestInvocation(command, args);
  if (directRewrite.rewritten || !isHeartbeatWrapper(command, args)) {
    return directRewrite;
  }

  const separatorIndex = args.indexOf("--");
  if (separatorIndex === -1 || separatorIndex === args.length - 1) {
    return directRewrite;
  }

  const wrappedCommand = String(args[separatorIndex + 1] ?? "");
  const wrappedArgs = args.slice(separatorIndex + 2);
  const wrappedRewrite = rewriteDirectPytestInvocation(wrappedCommand, wrappedArgs);
  if (!wrappedRewrite.rewritten) {
    return directRewrite;
  }

  return {
    command,
    args: [
      ...args.slice(0, separatorIndex + 1),
      wrappedRewrite.command,
      ...wrappedRewrite.args,
    ],
    rewritten: true,
  };
}

export {
  isPytestInvocation,
  prefersLocalVenvPytest,
  pythonExecutableSupportsPytest,
  rewriteDirectPytestInvocation,
};

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
  const [rawCommand, ...rawArgs] = lane.command;
  const rewritten = prefersLocalVenvPytest(rawCommand, rawArgs);
  const command = rewritten.command;
  const args = rewritten.args;
  const commandDisplay = [command, ...args].join(" ");
  if (rewritten.rewritten) {
    process.stdout.write(`run-bounded: using ${command} for pytest lane execution\n`);
  }
  const startedAtMs = Date.now();
  let lastOutputAtMs = Date.now();
  let terminatedReason = "";
  let diagnosticsPath = "";
  let watchdogSettled = false;
  const childEnv = { ...process.env };
  const caBundle = resolvePythonCaBundle(command, args, childEnv);
  if (isReadableFile(caBundle)) {
    childEnv.SSL_CERT_FILE = caBundle;
  }
  if (isPytestInvocation(command, args)) {
    childEnv.ZETHERION_DISABLE_ENV_FILE = "1";
  }

  const child = spawn(command, args, {
    cwd: process.cwd(),
    env: childEnv,
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

const entrypoint = process.argv[1] ? path.resolve(process.argv[1]) : "";
const currentFile = path.resolve(fileURLToPath(import.meta.url));

if (entrypoint === currentFile) {
  run().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  });
}
