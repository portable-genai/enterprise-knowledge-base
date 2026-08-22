#!/usr/bin/env node
import { spawn } from "node:child_process";

const requestedPort = process.argv[2] ?? "0";
if (!/^\d+$/.test(requestedPort)) throw new Error("port must be a non-negative integer");
const deadline = Date.now() + 60_000;
const server = spawn("npx", ["next", "start", "-p", requestedPort], {
  env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
let log = "";
let port = null;
let exited = false;
function capture(chunk) {
  const text = chunk.toString();
  log += text;
  const match = text.match(/http:\/\/localhost:(\d+)/);
  if (match) port = Number(match[1]);
}
server.stdout.on("data", capture);
server.stderr.on("data", capture);
server.on("exit", () => { exited = true; });

try {
  while (port === null && !exited && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (port === null) throw new Error(`this Next child never reported a bound port\n${log}`);
  if (requestedPort !== "0" && port !== Number(requestedPort)) {
    throw new Error(`requested ${requestedPort}, but this child bound ${port}`);
  }
  let response = null;
  while (!response && !exited && Date.now() < deadline) {
    try { response = await fetch(`http://127.0.0.1:${port}/`); }
    catch { await new Promise((resolve) => setTimeout(resolve, 100)); }
  }
  if (!response || exited) throw new Error(`this Next child did not serve its document\n${log}`);
  const csp = response.headers.get("content-security-policy") ?? "";
  const html = await response.text();
  const nonce = csp.match(/'nonce-([^']+)'/)?.[1];
  if (!nonce) throw new Error(`the response CSP carries no nonce: ${csp}`);
  const scripts = html.match(/<script\b[^>]*>/g) ?? [];
  if (!scripts.length) throw new Error("the document carries no scripts");
  const bare = scripts.filter((tag) => !tag.includes(`nonce="${nonce}"`));
  if (bare.length) throw new Error(`${bare.length} of ${scripts.length} scripts lack the served nonce`);
  console.log(`OK ${scripts.length} script tags carry this child's served nonce`);
} finally {
  server.kill("SIGTERM");
}
