/**
 * Vite config with API proxy for development.
 *
 * Dev: This proxy handles /api/* requests during `pnpm dev`.
 * Prod: server.js handles /api/* requests in production and docker-compose.yml.
 *
 * See also: server.js (production proxy), README.md (API proxy docs)
 */
import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter(), tsconfigPaths()],
  optimizeDeps: {
    include: ["docx-preview"],
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        secure: false,
        ws: true,
      },
    },
  },
});
