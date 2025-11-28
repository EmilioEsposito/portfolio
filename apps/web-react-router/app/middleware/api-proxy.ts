import type { Route } from "../+types/root";

/**
 * Helper function to get the backend URL for use in server-side loaders/actions.
 *
 * - Railway: Uses internal networking (CUSTOM_RAILWAY_BACKEND_URL)
 * - Docker Compose: Uses Docker service name
 * - Local: Uses localhost
 *
 * @example
 * ```typescript
 * export async function loader() {
 *   const backendUrl = getBackendUrl();
 *   const response = await fetch(`${backendUrl}/api/data`);
 *   return response.json();
 * }
 * ```
 */
export function getBackendUrl(): string {
  // Railway hosted (Production & Development)
  if (process.env.RAILWAY_ENVIRONMENT_NAME) {
    return process.env.CUSTOM_RAILWAY_BACKEND_URL || "http://localhost:8000";
  }

  // Docker Compose
  if (process.env.DOCKER_ENV) {
    return "http://fastapi:8000";
  }

  // Local development (default)
  return "http://127.0.0.1:8000";
}

/**
 * Middleware to proxy /api/* requests to the FastAPI backend.
 *
 * In local development: Uses localhost backend (Vite proxy also works)
 * In Railway: Uses Railway internal networking for fast server-to-server communication
 * In Docker Compose: Uses Docker network
 *
 * This intercepts ALL /api/* requests at the server level.
 */
export const apiProxyMiddleware: Route.MiddlewareFunction = async (
  { request },
  next
) => {
  const url = new URL(request.url);

  // Only intercept /api/* paths
  if (!url.pathname.startsWith("/api/")) {
    return next(); // Pass through to next middleware/route handler
  }

  const backendUrl = getBackendUrl();
  const proxyUrl = `${backendUrl}${url.pathname}${url.search}`;

  console.log(`[API Proxy] ${request.method} ${url.pathname} -> ${proxyUrl}`);

  try {
    // Clone headers but remove host (will be set by fetch)
    const headers = new Headers(request.headers);
    headers.delete("host");

    // Forward the request to FastAPI backend
    const response = await fetch(proxyUrl, {
      method: request.method,
      headers,
      body: request.body,
      // @ts-expect-error - duplex is needed for streaming but not in TS types yet
      duplex: "half", // Required for streaming request bodies
    });

    // Return the response (preserving streaming for SSE/chat endpoints)
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  } catch (error) {
    console.error(`[API Proxy] Error proxying request to ${proxyUrl}:`, error);
    return new Response(
      JSON.stringify({
        error: "Proxy Error",
        detail: error instanceof Error ? error.message : "Unknown error",
      }),
      {
        status: 502,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
};
