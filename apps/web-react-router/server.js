/**
 * Custom Express server for React Router v7.
 *
 * API Proxy: Forwards /api/* requests to FastAPI backend.
 * - Docker Compose: LOCAL_DOCKER_COMPOSE=true → http://fastapi:8000 (checked first)
 * - Railway: CUSTOM_RAILWAY_BACKEND_URL
 * - Local: http://127.0.0.1:${BACKEND_PORT} (default: 8000)
 *
 * Environment variables:
 *   BACKEND_PORT - FastAPI backend port for local dev (default: 8000)
 *
 * See also: vite.config.ts (dev proxy), README.md, docker-compose.yml
 */
import { createRequestHandler } from "@react-router/express";
import express from "express";
import compression from "compression";

const app = express();

// Compression for all responses
app.use(compression());

// Serve static assets from the client build
app.use(express.static("build/client", { maxAge: "1h" }));

/**
 * Wait until a downstream response can accept more data.
 * Returns false if the browser disconnects while backpressure is active.
 * @param {import("express").Response} res
 */
function waitForDrain(res) {
  if (res.destroyed || res.writableEnded) return Promise.resolve(false);

  return new Promise((resolve) => {
    const cleanup = () => {
      res.off("drain", onDrain);
      res.off("close", onClose);
      res.off("error", onClose);
    };
    const settle = (drained) => {
      cleanup();
      resolve(drained);
    };
    const onDrain = () => settle(true);
    const onClose = () => settle(false);

    res.once("drain", onDrain);
    res.once("close", onClose);
    res.once("error", onClose);

    // Close may have raced with listener registration.
    if (res.destroyed || res.writableEnded) settle(false);
  });
}

/**
 * Stream-proxy a request to the backend.
 * @param {import("express").Request} req
 * @param {import("express").Response} res
 * @param {string} targetUrl
 * @param {string} logTag
 */
async function proxyToBackend(req, res, targetUrl, logTag) {
  console.log(`[${logTag}] ${req.method} ${req.originalUrl} -> ${targetUrl}`);

  try {
    const headers = { ...req.headers };
    delete headers.host;

    const response = await fetch(targetUrl, {
      method: req.method,
      headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : req,
      duplex: "half",
    });

    for (const [key, value] of response.headers.entries()) {
      if (!["content-encoding", "transfer-encoding", "content-length"].includes(key.toLowerCase())) {
        res.setHeader(key, value);
      }
    }

    res.status(response.status);
    res.flushHeaders?.();

    if (response.body) {
      const reader = response.body.getReader();
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          // If the browser disconnected, keep draining the upstream stream so
          // the backend can finish its work and persist the conversation.
          if (res.destroyed || res.writableEnded) continue;

          if (!res.write(value) && !(await waitForDrain(res))) continue;
          if (!res.destroyed && !res.writableEnded) res.flush?.();
        }

        if (!res.destroyed && !res.writableEnded) {
          res.end();
        }
      } finally {
        reader.releaseLock();
      }
    } else {
      res.end();
    }
  } catch (error) {
    console.error(`[${logTag}] Error proxying to ${targetUrl}:`, error);
    if (!res.headersSent) {
      res.status(502).json({
        error: "Proxy Error",
        detail: error instanceof Error ? error.message : "Unknown error",
      });
    } else if (!res.destroyed && !res.writableEnded) {
      // Headers (and possibly part of an SSE response) are already on the
      // wire, so an HTTP error body is no longer valid. Terminate the socket
      // to make the browser's fetch fail instead of hanging indefinitely.
      res.destroy();
    }
  }
}

// API Proxy: Forward /api/* requests to the backend
app.use("/api", async (req, res) => {
  const backendUrl = getBackendUrl();
  const targetUrl = `${backendUrl}${req.originalUrl}`;
  await proxyToBackend(req, res, targetUrl, "API Proxy");
});

// React Router handler for all other requests
app.all(
  "*",
  createRequestHandler({
    build: () => import("./build/server/index.js"),
  })
);

function getBackendUrl() {
  // Docker Compose (set in docker-compose.yml) - check first to override env_file
  if (process.env.LOCAL_DOCKER_COMPOSE) {
    return "http://fastapi:8000";
  }

  // Railway
  if (process.env.CUSTOM_RAILWAY_BACKEND_URL) {
    return process.env.CUSTOM_RAILWAY_BACKEND_URL;
  }

  // Local development (supports worktree port isolation)
  const backendPort = process.env.BACKEND_PORT || "8000";
  return `http://127.0.0.1:${backendPort}`;
}

const port = process.env.PORT || 5173;

app.listen(port, () => {
  console.log(`Server listening on port ${port}`);
  console.log(`Backend URL: ${getBackendUrl()}`);
});
