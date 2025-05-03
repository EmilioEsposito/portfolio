require('dotenv').config({ path: '../../.env.development.local' }); // Load .env from root

// Load and validate Clerk key
const clerkPublishableKey = process.env.EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY;
if (!clerkPublishableKey) {
  throw new Error('Missing EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY. Ensure it is set in .env file and loaded in app.config.js');
}

// Load and validate the backend URL
const customRailwayBackendUrl = process.env.CUSTOM_RAILWAY_BACKEND_URL;
if (!customRailwayBackendUrl) {
  throw new Error('Missing CUSTOM_RAILWAY_BACKEND_URL. Ensure it is set in .env file and loaded in app.config.js');
}

module.exports = {
  "expo": {
    "name": "my-expo-app",
    "slug": "my-expo-app",
    "version": "1.0.0",
    "orientation": "portrait",
    "icon": "./assets/images/icon.png",
    "scheme": "myexpoapp",
    "userInterfaceStyle": "automatic",
    "newArchEnabled": true,
    "ios": {
      "supportsTablet": true
    },
    "android": {
      "adaptiveIcon": {
        "foregroundImage": "./assets/images/adaptive-icon.png",
        "backgroundColor": "#ffffff"
      },
      "edgeToEdgeEnabled": true
    },
    "web": {
      "bundler": "metro",
      "output": "static",
      "favicon": "./assets/images/favicon.png"
    },
    "plugins": [
      "expo-router",
      [
        "expo-splash-screen",
        {
          "image": "./assets/images/splash-icon.png",
          "imageWidth": 200,
          "resizeMode": "contain",
          "backgroundColor": "#ffffff"
        }
      ]
    ],
    "experiments": {
      "typedRoutes": true
    },
    "extra": {
      "clerkPublishableKey": clerkPublishableKey,
      "apiBaseUrl": customRailwayBackendUrl,
      "eas": {
        "projectId": "27de649a-d093-4196-a459-e6f1d94ed905"
      }
    }
  }
}
