/**
 * Where a route loader on the server reaches the API: the same backend the
 * /api proxy in server.js forwards to, chosen by the same rules (Docker
 * Compose, then CUSTOM_RAILWAY_BACKEND_URL on Railway, then the local
 * BACKEND_PORT). Keep the two in step.
 */
export function backendUrl(): string {
  if (process.env.LOCAL_DOCKER_COMPOSE) return "http://fastapi:8000";
  if (process.env.CUSTOM_RAILWAY_BACKEND_URL) return process.env.CUSTOM_RAILWAY_BACKEND_URL.replace(/\/+$/, "");
  return `http://127.0.0.1:${process.env.BACKEND_PORT || "8000"}`;
}
