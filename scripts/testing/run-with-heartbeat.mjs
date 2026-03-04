#!/usr/bin/env node

import { spawn } from "node:child_process";

function parseArgs(argv) {
  let heartbeatSeconds = 30;
  let commandStart = argv.indexOf("--");

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--heartbeat-seconds") {
      const raw = argv[i + 1];
      const parsed = Number.parseInt(String(raw), 10);
      if (!Number.isFinite(parsed) || parsed <= 0) {
        throw new Error("--heartbeat-seconds must be a positive integer");
      }
      heartbeatSeconds = parsed;
      i += 1;
      continue;
    }
    if (arg === "--") {
      commandStart = i;
      break;
    }
  }

  if (commandStart === -1 || commandStart === argv.length - 1) {
    throw new Error("Usage: run-with-heartbeat.mjs [--heartbeat-seconds N] -- <command> [args...]");
  }

  const command = argv[commandStart + 1];
  const args = argv.slice(commandStart + 2);
  return { heartbeatSeconds, command, args };
}

function nowIso() {
  return new Date().toISOString();
}

async function main() {
  const { heartbeatSeconds, command, args } = parseArgs(process.argv.slice(2));
  const startedAtMs = Date.now();
  let lastOutputAtMs = Date.now();

  const child = spawn(command, args, {
    stdio: ["ignore", "pipe", "pipe"],
    detached: false,
    env: process.env,
  });

  const heartbeatTimer = setInterval(() => {
    const silenceMs = Date.now() - lastOutputAtMs;
    if (silenceMs >= heartbeatSeconds * 1000) {
      const elapsed = Math.floor((Date.now() - startedAtMs) / 1000);
      process.stderr.write(
        `[heartbeat ${nowIso()}] command="${command}" elapsed=${elapsed}s silence=${Math.floor(
          silenceMs / 1000,
        )}s\n`,
      );
      lastOutputAtMs = Date.now();
    }
  }, 1000);

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

  const forwardSignal = (signal) => {
    child.kill(signal);
  };
  process.on("SIGINT", () => forwardSignal("SIGINT"));
  process.on("SIGTERM", () => forwardSignal("SIGTERM"));

  const exitCode = await new Promise((resolve) => {
    child.on("exit", (code, signal) => {
      if (typeof code === "number") {
        resolve(code);
        return;
      }
      if (signal) {
        resolve(128);
        return;
      }
      resolve(1);
    });
    child.on("error", () => resolve(1));
  });

  clearInterval(heartbeatTimer);
  process.exit(exitCode);
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(1);
});

