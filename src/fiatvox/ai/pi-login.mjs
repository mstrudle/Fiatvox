// Fiat Vox's bridge to Pi's sign-in, run with Fiat Vox's own Node and Pi.
//
// Pi's command line only offers sign-in inside its own interface (/login). Like Feynman, Fiat Vox calls Pi's SDK
// instead, so the sign-in runs inside the Fiat Vox setup screen. This script speaks JSON lines:
//   stdout: {"event": "providers" | "auth_url" | "device_code" | "info" | "progress" | "prompt" | "done" | "error", ...}
//   stdin:  {"id": <prompt id>, "value": "<answer>"}  or  {"id": <prompt id>, "cancel": true}
//
// Usage: node pi-login.mjs providers | login <provider> <oauth|api_key> | logout <provider> | models
// Environment: PI_SDK (absolute path to Pi's dist/index.js), PI_CODING_AGENT_DIR (Fiat Vox's isolated Pi folder).

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";

const send = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");

const sdkPath = process.env.PI_SDK;
const agentDir = process.env.PI_CODING_AGENT_DIR;
if (!sdkPath || !agentDir) {
  send({ event: "error", message: "PI_SDK and PI_CODING_AGENT_DIR must be set" });
  process.exit(2);
}
mkdirSync(agentDir, { recursive: true });
const { ModelRuntime, SettingsManager } = await import(pathToFileURL(sdkPath).href);
const authPath = join(agentDir, "auth.json");
const runtime = await ModelRuntime.create({ authPath, modelsPath: join(agentDir, "models.json") });

// answers to prompts arrive on stdin
const pending = new Map();
let nextId = 1;
const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
  const p = pending.get(msg.id);
  if (!p) return;
  pending.delete(msg.id);
  if (msg.cancel) p.reject(new Error("cancelled"));
  else p.resolve(String(msg.value ?? ""));
});

function ask(prompt) {
  const id = nextId++;
  const { signal, ...rest } = prompt;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    // a manual_code prompt is withdrawn when the browser callback wins the race
    signal?.addEventListener("abort", () => {
      if (pending.delete(id)) {
        send({ event: "prompt_withdrawn", id });
        reject(new Error("aborted"));
      }
    });
    send({ event: "prompt", id, ...rest });
  });
}

const interaction = {
  prompt: ask,
  notify: (event) => send({ event: event.type, ...event }),
};

async function providers() {
  const creds = new Set((await runtime.listCredentials()).map((c) => c.providerId));
  const list = runtime.getProviders().map((p) => ({
    id: p.id,
    name: p.name ?? p.id,
    oauth: Boolean(p.auth?.oauth),
    oauthName: p.auth?.oauth?.name ?? null,
    apiKey: Boolean(p.auth?.apiKey?.login),
    signedIn: creds.has(p.id),
  }));
  send({ event: "providers", providers: list });
}

async function models() {
  await runtime.refresh({ allowNetwork: true, signal: AbortSignal.timeout(15000) }).catch(() => undefined);
  const available = (await runtime.getAvailable?.()) ?? runtime.getAvailableModels?.() ?? [];
  send({ event: "models", models: available.map((m) => ({ provider: m.provider, id: m.id, name: m.name ?? m.id })) });
}

const [cmd, providerId, method] = process.argv.slice(2);
try {
  if (cmd === "providers") {
    await providers();
  } else if (cmd === "models") {
    await models();
  } else if (cmd === "login") {
    const settings = SettingsManager.create(process.cwd(), agentDir);
    const deviceId = settings.getOrCreateDeviceId();
    await settings.flush();
    await runtime.login(providerId, method || "oauth", interaction, { getDeviceId: () => deviceId });
    await runtime.refresh({ providers: [providerId], allowNetwork: true, signal: AbortSignal.timeout(15000) })
      .catch(() => undefined);
    send({ event: "done", provider: providerId });
  } else if (cmd === "logout") {
    await runtime.logout(providerId);
    send({ event: "done", provider: providerId });
  } else {
    send({ event: "error", message: `unknown command: ${cmd}` });
    process.exitCode = 2;
  }
} catch (err) {
  let message = err instanceof Error ? err.message : String(err);
  if (/invalid_grant|Missing authorization code/.test(message)) {
    message += ". Sign out of the provider in your browser (or use a private window) and try again.";
  }
  send({ event: "error", message });
  process.exitCode = 1;
} finally {
  rl.close();
}
