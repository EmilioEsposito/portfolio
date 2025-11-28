import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import tsconfigPaths from "vite-tsconfig-paths";

// Determine backend URL based on environment
const getBackendUrl = () => {
  // Railway hosted (Production & Development)
  console.log(">>> RAILWAY_ENVIRONMENT_NAME: ", process.env.RAILWAY_ENVIRONMENT_NAME);
  console.log(">>> CUSTOM_RAILWAY_BACKEND_URL: ", process.env.CUSTOM_RAILWAY_BACKEND_URL);
  if (process.env.RAILWAY_ENVIRONMENT_NAME) {
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
console.log(`>>> Vite proxy configured: /api/* -> ${backendUrl}/api/*`);

export default defineConfig({
  plugins: [tailwindcss(), reactRouter(), tsconfigPaths()],
  server: {
    proxy: {
      "/api": {
        target: backendUrl,
        changeOrigin: true,
        secure: false,
        ws: true,
        autoRewrite: true,
        followRedirects: true,
      },
    },
  },
});
