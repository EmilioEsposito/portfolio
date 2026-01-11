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

// API Proxy: Forward /api/* requests to the backend
app.use("/api", async (req, res) => {
  const backendUrl = getBackendUrl();
  const targetUrl = `${backendUrl}${req.originalUrl}`;

  console.log(`[API Proxy] ${req.method} ${req.originalUrl} -> ${targetUrl}`);

  try {
    // Build headers, excluding host
    const headers = { ...req.headers };
    delete headers.host;

    // Forward the request
    const response = await fetch(targetUrl, {
      method: req.method,
      headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : req,
      duplex: "half", // Required for streaming request bodies
    });

    // Copy response headers
    for (const [key, value] of response.headers.entries()) {
      // Skip headers that Express will set
      if (!["content-encoding", "transfer-encoding", "content-length"].includes(key.toLowerCase())) {
        res.setHeader(key, value);
      }
    }

    // Set status and stream body
    res.status(response.status);

    if (response.body) {
      const reader = response.body.getReader();
      const pump = async () => {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          res.write(value);
        }
        res.end();
      };
      pump().catch((err) => {
        console.error("[API Proxy] Stream error:", err);
        if (!res.headersSent) {
          res.status(502).json({ error: "Proxy Error", detail: err.message });
        }
      });
    } else {
      res.end();
    }
  } catch (error) {
    console.error(`[API Proxy] Error proxying to ${targetUrl}:`, error);
    if (!res.headersSent) {
      res.status(502).json({
        error: "Proxy Error",
        detail: error instanceof Error ? error.message : "Unknown error",
      });
    }
  }
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
