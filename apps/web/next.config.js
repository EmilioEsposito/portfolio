const path = require('path');
require('dotenv').config({ path: path.resolve(__dirname, '../../.env.development.local') });
// Also try to load a general .env.local or .env if specific development one isn't found or for other vars
require('dotenv').config({ path: path.resolve(__dirname, '../../.env.local'), override: false });
require('dotenv').config({ path: path.resolve(__dirname, '../../.env'), override: false });

// Log a few key variables to check if they are loaded
console.log(">>> [Config Start] Attempting to load .env from root.");
console.log(">>> [Config Start] NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY from process.env:", process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);
console.log(">>> [Config Start] CUSTOM_RAILWAY_BACKEND_URL from process.env:", process.env.CUSTOM_RAILWAY_BACKEND_URL);

const { withExpo } = require('@expo/next-adapter');
// const { withPlugins } = require('next-compose-plugins'); // Removed
// const withImages = require('next-images'); // Removed for now

/** @type {import('next').NextConfig} */
const baseConfig = {
  // Your existing config options remain here
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
  // Add other base configurations here if needed
  reactStrictMode: true,
  // Adding custom webpack configuration
  webpack: (config, { isServer }) => {
    // Add a rule to handle .ttf files
    config.module.rules.push({
      test: /\.ttf$/,
      use: [
        {
          loader: 'file-loader',
          options: {
            name: '[name].[ext]',
            outputPath: 'static/fonts/', // Output path for fonts
            publicPath: '/_next/static/fonts/', // Public path for fonts
          },
        },
      ],
    });

    // Important: return the modified config
    return config;
  },
};

// Wrap the base config with Expo adapter and add necessary transpilation
module.exports = withExpo({
  ...baseConfig,
  // Required for Expo packages and shared workspaces
  transpilePackages: [
    'react-native',
    'expo',
    '@expo/vector-icons',
    'expo-modules-core',
    // Add other Expo/RN packages here if needed
    '@portfolio/features',
    '@portfolio/ui',
  ],
  experimental: {
    forceSwcTransforms: true, // Recommended for Expo/RN
  },
});


// // Old configuration using next-compose-plugins
// // Initialize Expo adapter
// const withExpoAdapter = createExpoWebpackConfig(__dirname); // Incorrect usage
//
// // Combine plugins
// module.exports = withPlugins(
//   [
//     [withImages, { projectRoot: __dirname }], // Process images
//     withExpoAdapter, // Apply Expo configurations (Incorrect usage)
//   ],
//   nextConfig // Your existing Next.js config
// );
