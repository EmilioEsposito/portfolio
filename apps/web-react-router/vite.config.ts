/**
 * Vite config with API proxy for development.
 *
 * Dev: This proxy handles /api/* requests during `pnpm dev`.
 * Prod: server.js handles /api/* requests in production and docker-compose.yml.
 *
 * Environment variables (from .env in this directory):
 *   BACKEND_PORT - FastAPI backend port (default: 8000)
 *   VITE_PORT - Vite dev server port (default: 5173)
 *
 * See also: server.js (production proxy), README.md (API proxy docs)
 */
import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, loadEnv } from "vite";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig(({ mode }) => {
  // Load env from this directory (apps/web-react-router)
  const env = loadEnv(mode, process.cwd(), "");
  const backendPort = env.BACKEND_PORT || "8000";
  const vitePort = env.VITE_PORT ? parseInt(env.VITE_PORT, 10) : 5173;

  return {
    plugins: [tailwindcss(), reactRouter(), tsconfigPaths()],
    optimizeDeps: {
      include: ["docx-preview"],
    },
    server: {
      port: vitePort,
      proxy: {
        "/api": {
          target: `http://127.0.0.1:${backendPort}`,
          changeOrigin: true,
          secure: false,
          ws: true,
        },
      },
    },
  };
});
