/** @type {import('next').NextConfig} */
const nextConfig = {
  rewrites: async () => {
    // Check for the *presence* of the environment variable
    const dockerEnvVarExists = !!process.env.DOCKER_ENV;
    const railwayEnvVarExists = !!process.env.CUSTOM_RAILWAY_BACKEND_URL;

    console.log(`>>> next.config.js rewrite check: NODE_ENV=${process.env.NODE_ENV}, DOCKER_ENV=${process.env.DOCKER_ENV}, dockerEnvVarExists=${dockerEnvVarExists}`);

    let backendDestination;

    if (railwayEnvVarExists) {
      // Use HTTP for internal Railway communication
      // Railway's internal DNS routes fastapi.railway.internal to the correct container and PORT.
      backendDestination = `${process.env.CUSTOM_RAILWAY_BACKEND_URL}/api/:path*`;
    } else if (dockerEnvVarExists) {
      // Assume if the variable exists, we are in Docker Compose
      backendDestination = "http://fastapi:8000/api/:path*";
    } else if (process.env.NODE_ENV === "development") {
      // Local development outside Docker
      backendDestination = "http://127.0.0.1:8000/api/:path*";
    } else {
      // Production/Vercel (Variable doesn't exist)
      backendDestination = "/api/";
    }

    console.log(`>>> Applying rewrite /api/* to: ${backendDestination}`);

    return [
      {
        source: "/api/:path*",
        destination: backendDestination,
      },
    ];
  },
  env: {
    NEXT_PUBLIC_ENV_VAR_EXAMPLE: process.env.NEXT_PUBLIC_ENV_VAR_EXAMPLE,
    NEXT_PUBLIC_GOOGLE_DRIVE_PICKER_API_KEY: process.env.NEXT_PUBLIC_GOOGLE_DRIVE_PICKER_API_KEY,
    NEXT_PUBLIC_GOOGLE_CLIENT_ID: process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID,
  },
};

module.exports = nextConfig;
