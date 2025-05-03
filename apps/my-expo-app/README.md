# Expo App (my-expo-app)

This is the React Native application built with [Expo](https://expo.dev) SDK 53, part of the `portfolio` monorepo.

## Overview & Context

*   **Purpose:** Provides native mobile interfaces (iOS & Android) for the portfolio project, potentially sharing components/features with the `apps/web` Next.js application via packages in the `packages/` directory.
*   **Monorepo:** Managed using `pnpm` workspaces. Dependencies are installed from the workspace root (`/Users/eesposito/portfolio`).
*   **Routing:** Uses [Expo Router](https://docs.expo.dev/router/introduction/) for file-based routing within the `app/` directory.
*   **Authentication:** Uses [Clerk](https://clerk.com/) via `@clerk/clerk-expo`.
*   **Backend:** Communicates with the FastAPI backend located in the `api/` directory.
*   **Builds & Updates:** Uses [Expo Application Services (EAS)](https://expo.dev/eas) for building (`eas build`) and potentially updates (`eas update`).

## Getting Started (Local Development)

1.  **Install Dependencies:**
    *   Ensure you are in the workspace root (`/Users/eesposito/portfolio`).
    *   Run `pnpm install` to install dependencies for all workspaces.

2.  **Update Local Backend IP (If running backend locally):
    *   The app needs to know the IP address of the machine running the FastAPI backend for local development.
    *   Run the script defined in the root `README.md` (or `make update-local-ip`) to update the `CUSTOM_RAILWAY_BACKEND_URL` in the root `.env.development.local` file with your current local network IP.

3.  **Start the Development Server:**
    *   Navigate to this app's directory: `cd apps/my-expo-app`
    *   Run `pnpm expo start` (or `pnpm start` via package.json script).

4.  **Run the App:**
    *   Use the Expo Go app on your physical device (scan QR code).
    *   Run on an [iOS simulator](https://docs.expo.dev/workflow/ios-simulator/) (press `i` in the terminal running `expo start`). Requires Xcode.
    *   Run on an [Android emulator](https://docs.expo.dev/workflow/android-studio-emulator/) (press `a` in the terminal running `expo start`). Requires Android Studio.
    *   Use a [Development Build](https://docs.expo.dev/develop/development-builds/introduction/) created via `eas build --profile local --local` (see below).

## Configuration

*   **`app.config.js`:** Dynamic configuration file. Reads environment variables during build time (`eas build`) or runtime (`expo start`) to configure the app (e.g., Clerk keys, API URL, EAS Update settings).
    *   For local development (`expo start` or `eas build --local`), it loads variables from the **root** `.env.development.local` via `dotenv`.
    *   For cloud builds (`eas build`), it reads environment variables set by the corresponding profile in `eas.json`.
*   **`eas.json`:** Configures EAS Build profiles (`local`, `development`, `production`). Defines environment variables for cloud builds, often referencing **EAS Secrets** for sensitive values.
*   **Environment Variables & Secrets:**
    *   `EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY`: Public key for Clerk frontend.
    *   `CUSTOM_RAILWAY_BACKEND_URL`: Base URL for the FastAPI backend.
    *   Use `eas secret create ...` to store sensitive production/development keys/URLs. Reference them in `eas.json` using `${secrets.SECRET_NAME}`.
        *   Example commands (run from workspace root or `apps/my-expo-app`):
          ```bash
          # Development Secrets
          eas secret create --scope project --name DEV_EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY --value <PASTE_DEV_CLERK_KEY> --type string
          eas secret create --scope project --name DEV_CUSTOM_RAILWAY_BACKEND_URL --value <PASTE_DEV_BACKEND_URL> --type string
          
          # Production Secrets
          eas secret create --scope project --name PROD_EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY --value <PASTE_PROD_CLERK_KEY> --type string
          eas secret create --scope project --name PROD_CUSTOM_RAILWAY_BACKEND_URL --value <PASTE_PROD_BACKEND_URL> --type string
          ```
    *   The `local` profile in `eas.json` uses dummy values for env vars, as the actual values for local builds (`eas build --local --local`) are loaded dynamically via `app.config.js` from the root `.env.development.local` file.

## Building the App (EAS Build)

Native builds are created using EAS Build. Run commands from within this directory (`apps/my-expo-app`).

*   **Local Native Build (for Simulator/Device):**
    *   Requires native dependencies: Xcode, Command Line Tools, CocoaPods, Fastlane.
    *   Command: `eas build --profile local --local -p <ios|android>`
    *   Connects to the backend specified in the root `.env.development.local` (via `app.config.js`).
*   **Development Cloud Build (Internal Testing):**
    *   Command: `eas build --profile development -p <ios|android>`
    *   Uses environment variables/secrets defined in the `development` profile in `eas.json` (e.g., points to hosted dev backend).
*   **Production Cloud Build (Store Submission):**
    *   Command: `eas build --profile production -p <ios|android>`
    *   Uses environment variables/secrets defined in the `production` profile in `eas.json`.

## Native Build Dependencies (Local macOS Setup)

Running local native builds (`eas build --local`) requires certain tools to be installed on your macOS machine beyond the standard Node/pnpm setup. These tools interact with the native Xcode build process.

*   **Xcode & Command Line Tools:** Required for any iOS development. Install from the App Store and ensure Command Line Tools are installed (`xcode-select --install`).
*   **Fastlane:** Used by EAS to automate parts of the native build and signing process.
    *   Common Installation: `brew install fastlane` or `[sudo] gem install fastlane`.
*   **CocoaPods:** The dependency manager for native iOS libraries.
    *   Common Installation: `[sudo] gem install cocoapods`, followed potentially by `pod setup`.
*   **Ruby Environment:** Fastlane and CocoaPods are Ruby gems. Using a Ruby version manager like `rbenv` (`brew install rbenv ruby-build`) to install a modern Ruby version (e.g., 3.1+) is highly recommended over using the older system Ruby.

## Deployment & Distribution

*   **iOS:** Use `eas submit -p ios` (after a successful build) to upload to App Store Connect for TestFlight distribution or App Store release.
*   **Android:** Use `eas submit -p android` (after a successful build) to upload to Google Play Console.

## Useful Links

*   [Expo Documentation](https://docs.expo.dev/)
*   [Expo Router Documentation](https://docs.expo.dev/router/introduction/)
*   [EAS Build Documentation](https://docs.expo.dev/build/introduction/)
*   [EAS Update Documentation](https://docs.expo.dev/eas-update/introduction/)
*   [Clerk Expo Documentation](https://clerk.com/docs/references/expo)

## Push Notification Flow (End-to-End)

This section describes how push notifications are registered and sent.

**1. Token Registration (Client-Side on Sign-In):**
   *   When a user successfully signs in (`isSignedIn` becomes true in `app/_layout.tsx`):
       *   The app calls `registerForPushNotificationsAsync` (`utils/notifications.ts`).
       *   This function requests OS permissions (iOS/Android) for notifications.
       *   If permission is granted, it requests an **Expo Push Token** from Expo's servers using the `projectId` from `app.config.js`.
       *   If an Expo Push Token is obtained, the app then gets the current **Clerk Session Token** using `getToken()` from `useAuth`.
       *   Both the Expo Push Token and the Clerk Session Token are passed to `sendTokenToBackend` (`utils/notifications.ts`).
       *   `sendTokenToBackend` makes a `POST` request to the backend endpoint `/api/push/register` (URL determined by `CUSTOM_RAILWAY_BACKEND_URL` from `app.config.js`).
           *   **Request Body:** ` { "token_body": { "token": "ExponentPushToken[...]" } } `
           *   **Headers:** Includes `Authorization: Bearer <Clerk Session Token>`.

**2. Token Storage (Backend):**
   *   The FastAPI backend receives the request at the `/push/register` route (`api/src/push/routes.py`).
   *   The `Depends(get_auth_user)` dependency verifies  theClerk Session Token.
   *   If authentication succeeds, the route extracts the user's primary email address from the authenticated Clerk `User` object.
   *   It extracts the Expo Push Token from the `token_body.token` field in the request body.
   *   It calls `service.register_token` (`api/src/push/service.py`).
   *   `service.register_token` performs an "upsert" operation on the `push_tokens` database table: 
       *   It finds or creates a record with the given Expo Push Token.
       *   It ensures this record is associated with the authenticated user's email address.

**3. Sending a Notification (Backend Trigger):**
   *   *(Conceptual - Sending logic exists, but trigger mechanism might vary)*
   *   Some backend process (e.g., triggered by an event, admin action, scheduled job) decides to send a notification to a specific user (identified by `email`).
   *   This process calls `service.send_notification_to_email` (`api/src/push/service.py`) with the target email, title, body, etc.
   *   `service.send_notification_to_email` looks up all Expo Push Tokens associated with that `email` in the `push_tokens` table.
   *   For each token found, it calls `service.send_push_message`.
   *   `service.send_push_message` constructs the message payload required by Expo's Push API.
   *   It makes a `POST` request to Expo's Push API endpoint (`https://exp.host/--/api/v2/push/send`) with the recipient token and message details.

**4. Delivery via Expo & Platform Services:**
   *   Expo's Push Service receives the request from the backend.
   *   Expo sends the notification to the appropriate platform service (APNS for iOS, FCM for Android).
   *   APNS/FCM delivers the notification to the specific device associated with the Expo Push Token.

**5. Notification Handling (Client-Side):**
   *   If the app is in the **foreground**, the handler configured by `Notifications.setNotificationHandler` (`utils/notifications.ts`) is called. The current configuration allows the notification alert to be shown.
   *   If the app is in the **background** or **closed**, the device's OS displays the notification according to standard platform behavior.
