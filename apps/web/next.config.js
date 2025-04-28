const { withExpo } = require('@expo/next-adapter');
const path = require('path');

/** @type {import('next').NextConfig} */
let nextConfig = {
  rewrites: async () => {
    // Check for the *presence* of the environment variable
    const dockerEnvVarExists = !!process.env.DOCKER_ENV;
    const railwayEnvVarExists = !!process.env.CUSTOM_RAILWAY_BACKEND_URL;

    console.log(
      `>>> next.config.js rewrite check: NODE_ENV=${process.env.NODE_ENV}, DOCKER_ENV=${process.env.DOCKER_ENV}, dockerEnvVarExists=${dockerEnvVarExists}`
    );

    let backendDestination;

    if (railwayEnvVarExists) {
      // Railway hosted (Production & Development)
      backendDestination = `${process.env.CUSTOM_RAILWAY_BACKEND_URL}/api/:path*`;
    } else if (dockerEnvVarExists) {
      // Docker local 
      // Assume if the variable exists, we are in Docker Compose
      backendDestination = "http://fastapi:8000/api/:path*";
    } else if (process.env.NODE_ENV === "development" || process.env.NODE_ENV === "production") {
      // Non-Docker local
      // Local development outside Docker
      backendDestination = "http://127.0.0.1:8000/api/:path*";
    } else {
      // backendDestination = "/api/"; // OLD, Vercel used to use this
      // raise error
      throw new Error(
        "No backend destination found to proxy to. Please check your environment variables."
      );
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
    NEXT_PUBLIC_GOOGLE_DRIVE_PICKER_API_KEY:
      process.env.NEXT_PUBLIC_GOOGLE_DRIVE_PICKER_API_KEY,
    NEXT_PUBLIC_GOOGLE_CLIENT_ID: process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID,
  },
  transpilePackages: [
    'react-native',
    'react-native-web',
    'expo',
    // Add other Expo/RN packages you want to transpile here
  ],
};

// Configuration for Expo adapter
const expoConfig = {
  projectRoot: path.resolve(__dirname, '../..'), // Point to the monorepo root
};

// Wrap the Next.js config with the Expo adapter
module.exports = withExpo(nextConfig, expoConfig);
