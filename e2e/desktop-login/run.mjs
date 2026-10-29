#!/usr/bin/env node
// Desktop email sign-in, end to end, with nothing simulated between the two
// products: the real Desktop Main authentication service (PKCE, loopback
// listener, browser handoff receiver, Supabase client) from a Desktop
// checkout, and the real Cloud /login page in Chromium typing an email and
// password. Passing means a person could sign in to Desktop through this Web.
//
// Hermetic mode (PR checks): SUPABASE_URL + SUPABASE_ANON_KEY describe a local
// Supabase; the Desktop's Cloud API configuration call is answered locally.
// Deployed mode (post-deploy checks): API_BASE points at a real Cloud API and
// Desktop reads its Supabase configuration from there, as the app does.

import { mkdirSync, writeFileSync } from "node:fs";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright";

const env = process.env;
const required = (name) => {
  const value = env[name]?.trim();
  if (!value) fail(`Missing required environment variable ${name}.`);
  return value;
};

const desktopRoot = path.resolve(required("DESKTOP_ROOT"));
const webOrigin = new URL(required("WEB_ORIGIN")).origin;
const email = required("E2E_EMAIL");
const password = required("E2E_PASSWORD");
const apiBase = env.API_BASE?.trim() || "http://127.0.0.1:9/api/v1";
const deployed = Boolean(env.API_BASE?.trim());
const resultsDir = path.resolve(env.RESULTS_DIR || "results");
const timeoutMs = Number(env.E2E_TIMEOUT_MS || 90_000);
mkdirSync(resultsDir, { recursive: true });

const log = [];
const record = (kind, message, detail) => {
  const entry = { at: new Date().toISOString(), kind, message: redact(String(message)), ...(detail ? { detail: redact(JSON.stringify(detail)) } : {}) };
  log.push(entry);
  console.log(`[${entry.kind}] ${entry.message}${entry.detail ? ` ${entry.detail}` : ""}`);
};

const { createCloudAuthService } = await import(pathToFileURL(path.join(desktopRoot, "electron/cloud-auth-service.mjs")).href);
const requestCloudApi = deployed
  ? (await import(pathToFileURL(path.join(desktopRoot, "electron/main/cloud-api-client.mjs")).href)).requestCloudApi
  : hermeticCloudApi({ supabaseUrl: required("SUPABASE_URL"), anonKey: required("SUPABASE_ANON_KEY") });

const rendererEvents = [];
const rendererWindow = {
  isDestroyed: () => false,
  webContents: { send: (channel, payload) => rendererEvents.push({ channel, status: payload?.status, message: payload?.message }) },
};
let stored = null;
let loginUrl = null;
const service = createCloudAuthService({
  app: { getPath: () => mkdtempSync(path.join(tmpdir(), "desktop-login-e2e-")), getAppPath: () => desktopRoot },
  requestCloudApi,
  getWindows: () => [rendererWindow],
  revealWindow: () => record("desktop", "revealWindow"),
  externalNavigation: { open: async (url) => { loginUrl = url; } },
  localCloudWebUrl: webOrigin,
  credentialStore: { read: async () => stored, write: async (value) => { stored = value; }, clear: async () => { stored = null; } },
  logger: {
    error: (...args) => record("desktop-error", args.map(String).join(" ")),
    warn: (...args) => record("desktop-warn", args.map((arg) => typeof arg === "string" ? arg : JSON.stringify(arg)).join(" ")),
  },
});

const browser = await chromium.launch({ headless: env.HEADED !== "1" });
const page = await (await browser.newContext()).newPage();
page.on("console", (message) => { if (["error", "warning"].includes(message.type())) record("browser-console", message.text()); });
page.on("pageerror", (error) => record("browser-pageerror", error.message));

let exitCode = 0;
try {
  await step("Desktop starts sign-in", async () => {
    await service.startOAuth({ apiBase });
    if (!loginUrl) throw new Error("Desktop did not open a login URL.");
    const url = new URL(loginUrl);
    if (url.origin !== webOrigin || url.pathname !== "/login") throw new Error(`Desktop opened an unexpected URL origin/path: ${url.origin}${url.pathname}`);
    const state = await service.readState();
    if (state.status !== "signing-in") throw new Error(`Desktop status is ${state.status}, expected signing-in.`);
  });

  const callback = new URL(new URL(loginUrl).searchParams.get("desktop_callback"));
  let handoff = null;
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.origin === callback.origin && url.pathname === "/auth/session") handoff = response;
  });

  await step("Browser opens the Desktop login page", async () => {
    const response = await page.goto(loginUrl, { waitUntil: "domcontentloaded", timeout: timeoutMs });
    if (!response?.ok()) throw new Error(`Login page returned HTTP ${response?.status()}.`);
  });

  await step("Person enters email and password", async () => {
    const emailField = page.locator('input[type="email"]');
    await emailField.waitFor({ state: "visible", timeout: timeoutMs });
    await emailField.fill(email);
    await emailField.press("Enter");
    const passwordField = page.locator('input[type="password"]');
    await passwordField.waitFor({ state: "visible", timeout: timeoutMs });
    await passwordField.fill(password);
    await passwordField.press("Enter");
  });

  await step("Browser hands the session to Desktop", async () => {
    await page.waitForURL((url) => url.origin === callback.origin && url.pathname === "/auth/session", { timeout: timeoutMs });
    await page.waitForLoadState("domcontentloaded");
    if (!handoff) throw new Error("The browser reached the Desktop listener without a recorded response.");
    if (handoff.status() !== 200) throw new Error(`Desktop listener answered HTTP ${handoff.status()}: ${(await page.innerText("body")).slice(0, 300)}`);
  });

  await step("Desktop is signed in as this person", async () => {
    const deadline = Date.now() + timeoutMs;
    let state = await service.readState();
    while (state.status !== "authenticated" && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      state = await service.readState();
    }
    if (state.status !== "authenticated") throw new Error(`Desktop status is ${state.status}, expected authenticated.`);
    if (state.session?.user_email?.toLowerCase() !== email.toLowerCase()) throw new Error("Desktop is signed in as a different account.");
    if (!stored?.refresh_token || "access_token" in stored) throw new Error("Desktop did not persist exactly a refresh credential.");
    const errors = rendererEvents.filter((event) => event.channel === "cloud-auth:error");
    if (errors.length) throw new Error(`Desktop reported sign-in errors: ${errors.map((event) => event.message).join("; ")}`);
  });

  await step("Desktop signs out", async () => {
    await service.clearSession();
    if ((await service.readState()).status !== "signed-out") throw new Error("Desktop did not sign out.");
  });
} catch (error) {
  exitCode = 1;
  record("failure", error instanceof Error ? error.message : String(error));
  await page.screenshot({ path: path.join(resultsDir, "failure.png"), fullPage: true }).catch(() => {});
  const text = await page.innerText("body").catch(() => "");
  writeFileSync(path.join(resultsDir, "failure-page.txt"), `${redact(page.url())}\n\n${redact(text).slice(0, 4000)}\n`);
} finally {
  service.dispose();
  await browser.close().catch(() => {});
  const summary = {
    result: exitCode === 0 ? "passed" : "failed",
    mode: deployed ? "deployed" : "hermetic",
    webOrigin,
    apiBase: deployed ? apiBase : null,
    rendererEvents,
    log,
  };
  writeFileSync(path.join(resultsDir, "summary.json"), `${JSON.stringify(summary, null, 2)}\n`);
  console.log(`Desktop email sign-in: ${summary.result.toUpperCase()}`);
}
process.exit(exitCode);

async function step(name, action) {
  record("step", name);
  await action();
  record("step", `${name}: ok`);
}

function hermeticCloudApi({ supabaseUrl, anonKey }) {
  return async (_apiBase, apiPath) => {
    if (apiPath === "/auth/config") return { supabase_url: supabaseUrl, supabase_anon_key: anonKey };
    if (apiPath === "/auth/initialize") return { initialized: true };
    throw new Error(`Hermetic sign-in check does not serve Cloud API ${apiPath}.`);
  };
}

function redact(value) {
  return value
    .replace(/[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}/g, "[jwt]")
    .replace(/((?:refresh|access)_token|code_verifier|code_challenge|desktop_state|state|code|password)=[^&\s"]+/gi, "$1=[redacted]");
}

function fail(message) {
  console.error(message);
  process.exit(2);
}
