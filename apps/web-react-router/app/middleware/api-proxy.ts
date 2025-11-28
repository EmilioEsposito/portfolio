import type { Route } from "../+types/root";

/**
 * Middleware to proxy /api/* requests to the FastAPI backend.
 * 
 * In local development: Uses localhost backend (Vite proxy also works) (file: vite.config.ts)
 * In Railway hosted environments: Uses Railway internal networking for fast server-to-server communication (file: app/middleware/api-proxy.ts)
 * In Docker Compose production: Uses Docker Compose network for fast server-to-server communication (file: vite.config.ts)
 * 
 * This handles all client-side fetch requests to /api/*.
 * For server-side loader/action fetches, use the getBackendUrl() helper directly.
 */
export function apiProxyMiddleware(): Route.MiddlewareFunction {
  return async (context) => {
    const url = new URL(context.request.url);

    // Only intercept /api/* paths
    if (!url.pathname.startsWith("/api/")) {
      return; // Pass through to next middleware/route handler
    }

    // Determine backend URL based on environment
    const getBackendUrl = () => {
      // Railway hosted (Production & Development)
      if (process.env.RAILWAY_ENVIRONMENT_NAME) {
        // Use Railway internal networking for server-to-server calls (faster & free)
        return process.env.CUSTOM_RAILWAY_BACKEND_URL || "http://localhost:8000";
      }

      // Docker Compose
      if (process.env.DOCKER_ENV) {
        return "http://fastapi:8000";
      }

      // Local development (default)
      return "http://127.0.0.1:8000";
    };

    const backendUrl = getBackendUrl();
    const proxyUrl = `${backendUrl}${url.pathname}${url.search}`;

    console.log(`[API Proxy] ${context.request.method} ${url.pathname} -> ${proxyUrl}`);

    try {
      // Forward the request to FastAPI backend
      const response = await fetch(proxyUrl, {
        method: context.request.method,
        headers: context.request.headers,
        body: context.request.body,
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
}

/**
 * Helper function to get the backend URL for use in server-side loaders/actions.
 * 
 * Use this when making API calls from loaders/actions instead of relative URLs.
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

