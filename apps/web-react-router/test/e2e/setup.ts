/**
 * Ports, the database and the environment for the browser test's servers.
 * Shared by playwright.config.ts, serve.ts and the tests; no Playwright
 * imports, so Node can run serve.ts directly.
 *
 * Each server gets a clean environment built here, not the shell's: no
 * secret reaches it, Quo and Google are never configured, Turnstile uses
 * Cloudflare's test keys (which the API answers itself outside production),
 * and the send mode stays capture.
 */
import { fileURLToPath } from "node:url";

export const WEB_DIR = fileURLToPath(new URL("../..", import.meta.url));
export const REPO_ROOT = fileURLToPath(new URL("../../../..", import.meta.url));
export const PYTHON = `${REPO_ROOT}.venv/bin/python`;

export const API_PORT = Number(process.env.E2E_API_PORT || 8765);
export const WEB_PORT = Number(process.env.E2E_WEB_PORT || 5765);
/** A disposable database on this machine; the test resets its showing data. */
export const DATABASE_URL = process.env.E2E_DATABASE_URL || "postgresql://portfolio:portfolio@127.0.0.1:5432/portfolio_e2e";

/** The page is opened on a Sernia host, so the /api proxy forwards only the public booking API. */
export const BASE_URL = `http://serniacapital.localhost:${WEB_PORT}`;
/** The unit `scripts/showing_browser_test.py reset` puts on offer. */
export const UNIT = "900-01";
/** The fictional team number the API hands out after the bot check. */
export const TEAM_PHONE = "+14125550100";

function base(): Record<string, string> {
  const keep: Record<string, string> = {};
  for (const name of ["PATH", "HOME", "TMPDIR", "LANG", "TZ"]) {
    const value = process.env[name];
    if (value) keep[name] = value;
  }
  return keep;
}

/** The API's environment: the test database and dummy keys, nothing else. */
export function apiEnvironment(): Record<string, string> {
  return {
    ...base(),
    PORT: String(API_PORT),
    DATABASE_URL,
    DATABASE_URL_UNPOOLED: DATABASE_URL,
    DATABASE_REQUIRE_SSL: "false",
    PYTHON_DOTENV_DISABLED: "1",
    BACKGROUND_AUTOMATION_ENABLED: "false",
    LOGFIRE_SEND_TO_LOGFIRE: "false",
    // Made-up values for what the API reads at import time, as the root
    // conftest.py sets for pytest. None of them works against a real service.
    OPENAI_API_KEY: "test-openai-key",
    ANTHROPIC_API_KEY: "test-anthropic-key",
    LOCAL_PORTFOLIO_OPENROUTER_API_KEY: "test-openrouter-key",
    PUBLIC_PORTFOLIO_OPENROUTER_API_KEY: "test-public-openrouter-key",
    CLERK_SECRET_KEY: "test-clerk-secret",
    CLERK_WEBHOOK_SECRET: "test-clerk-webhook",
    TWILIO_ACCOUNT_SID: "test-twilio-sid",
    TWILIO_AUTH_TOKEN: "test-twilio-token",
    CLICKUP_API_KEY: "test-clickup-key",
    SESSION_SECRET_KEY: "test-session-secret",
    GOOGLE_OAUTH_CLIENT_ID: "test-google-client-id",
    GOOGLE_OAUTH_CLIENT_SECRET: "test-google-client-secret",
    GOOGLE_OAUTH_REDIRECT_URI: "http://localhost:8000/api/oauth/callback",
    QUO_WEBHOOK_SECRET: "test-openphone-webhook",
    TWILIO_ESCALATION_FROM_NUMBER: "+14125550199",
    SERNIA_TEAM_PHONE: TEAM_PHONE,
  };
}

/**
 * Made-up Clerk keys for the team's pages, which run Clerk's middleware and
 * need keys to answer at all. The publishable key names a Clerk host that does
 * not exist (clerk.example.test), so the browser's request for Clerk's script
 * is blocked like any other outside request, and with no session cookie the
 * server never calls Clerk: every team page is signed out.
 */
export const CLERK_HOST = "clerk.example.test";
const MADE_UP_CLERK_KEYS = {
  CLERK_PUBLISHABLE_KEY: `pk_live_${Buffer.from(`${CLERK_HOST}$`).toString("base64")}`,
  CLERK_SECRET_KEY: "sk_live_made-up-for-the-browser-test",
  CLERK_TELEMETRY_DISABLED: "1",
};

/** The web server's environment: where the API is, the pages switched on, and made-up Clerk keys. */
export function webEnvironment(): Record<string, string> {
  return {
    ...base(),
    ...MADE_UP_CLERK_KEYS,
    NODE_ENV: "production",
    PORT: String(WEB_PORT),
    BACKEND_PORT: String(API_PORT),
  };
}
