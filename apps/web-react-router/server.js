/**
 * Custom Express server for React Router v7.
 *
 * API Proxy: Forwards /api/* requests to FastAPI backend.
 * - Docker Compose: LOCAL_DOCKER_COMPOSE=true → http://fastapi:8000 (checked first)
 * - Railway: CUSTOM_RAILWAY_BACKEND_URL
 * - Local: http://127.0.0.1:8000
 *
 * See also: vite.config.ts (dev proxy), README.md, docker-compose.yml
 */
import { createRequestHandler } from "@react-router/express";
import express from "express";
import compression from "compression";
import * as logfire from "@pydantic/logfire-node";

// Configure Logfire for observability with auto-instrumentation
// This automatically instruments: Express, HTTP, fetch, and more
logfire.configure({
  token: process.env.LOGFIRE_TOKEN,
  serviceName: "web-react-router",
  serviceVersion: "1.0.0",
  environment: process.env.RAILWAY_ENVIRONMENT_NAME || "local",
  autoInstrument: true, // Enable auto-instrumentation for Express, HTTP, etc.
});

// Logger helper: sends to both Logfire and console (for local dev visibility)
const logger = {
  info: (message, attributes = {}) => {
    logfire.info(message, attributes);
    console.log(`[INFO] ${message}`, attributes);
  },
  error: (message, attributes = {}) => {
    logfire.error(message, attributes);
    console.error(`[ERROR] ${message}`, attributes);
  },
  warn: (message, attributes = {}) => {
    logfire.warn(message, attributes);
    console.warn(`[WARN] ${message}`, attributes);
  },
  debug: (message, attributes = {}) => {
    logfire.debug(message, attributes);
    if (process.env.NODE_ENV !== "production") {
      console.debug(`[DEBUG] ${message}`, attributes);
    }
  },
};

const app = express();

// Compression for all responses
app.use(compression());

// Serve static assets from the client build
app.use(express.static("build/client", { maxAge: "1h" }));

// API Proxy: Forward /api/* requests to the backend
app.use("/api", async (req, res) => {
  const backendUrl = getBackendUrl();
  const targetUrl = `${backendUrl}${req.originalUrl}`;

  logger.info("API Proxy request", {
    method: req.method,
    originalUrl: req.originalUrl,
    targetUrl,
  });

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
        logger.error("API Proxy stream error", {
          error: err.message,
          stack: err.stack,
        });
        if (!res.headersSent) {
          res.status(502).json({ error: "Proxy Error", detail: err.message });
        }
      });
    } else {
      res.end();
    }
  } catch (error) {
    logger.error("API Proxy error", {
      targetUrl,
      error: error instanceof Error ? error.message : "Unknown error",
      stack: error instanceof Error ? error.stack : undefined,
    });
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

  // Local development
  return "http://127.0.0.1:8000";
}

const port = process.env.PORT || 5173;

app.listen(port, () => {
  logger.info("Server started", {
    port,
    backendUrl: getBackendUrl(),
    environment: process.env.RAILWAY_ENVIRONMENT_NAME || "local",
  });
});
