// Railway Function entrypoint (Bun); also runs under Node for offline tests.
import { pathToFileURL } from "node:url";
import { setTimeout as sleep } from "node:timers/promises";

export async function refreshGmailWatch({
  env = process.env,
  fetchImpl = fetch,
  wait = sleep,
  logger = console,
} = {}) {
  const base = env.CUSTOM_RAILWAY_BACKEND_URL;
  const secret = env.CRON_SECRET;
  if (!base || !secret?.trim()) {
    throw new Error("CUSTOM_RAILWAY_BACKEND_URL and CRON_SECRET are required");
  }
  const url = new URL("/api/google/gmail/watch/refresh", base);
  const attempts = 3;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    let response;
    try {
      response = await fetchImpl(url, {
        method: "POST",
        headers: { Authorization: `Bearer ${secret}` },
        redirect: "error",
        // Allow the backend's four Google attempts and backoff to finish.
        signal: AbortSignal.timeout(300_000),
      });
    } catch {
      // Do not log fetch errors: they can contain credentials or private URLs.
      logger.error(`Gmail watch attempt ${attempt}/${attempts}: network error or timeout`);
      if (attempt === attempts) throw new Error("Gmail watch renewal failed after 3 attempts");
      await wait(2_000 * 2 ** (attempt - 1));
      continue;
    }
    logger.log(`Gmail watch attempt ${attempt}/${attempts}: HTTP ${response.status}`);
    if (response.ok) {
      const data = await response.json();
      if (data.success !== true) throw new Error("Gmail watch renewal reported failure");
      if (data.expiration == null && data.message?.startsWith("Skipping Gmail watch refresh")) {
        logger.log("Gmail watch refresh skipped by backend environment policy");
        return data;
      }
      if (!Number.isFinite(Number(data.expiration)) || Number(data.expiration) <= Date.now()
          || !data.historyId) {
        throw new Error("Gmail watch renewal returned no valid future expiration/historyId");
      }
      logger.log(`Gmail watch renewed; expiration=${data.expiration}`);
      return data;
    }
    // Never log the backend body: upstream error details may contain private data.
    await response.body?.cancel();
    const retryable = response.status === 408 || response.status === 429 || response.status >= 500;
    if (!retryable || attempt === attempts) {
      throw new Error(`Gmail watch renewal failed: HTTP ${response.status} after ${attempt} attempt(s)`);
    }
    await wait(2_000 * 2 ** (attempt - 1));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    await refreshGmailWatch();
  } catch {
    console.error("Gmail watch renewal failed; cron exiting with status 1");
    process.exitCode = 1;
  }
}
