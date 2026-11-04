# Mobile app

Expo SDK 53 / React Native client with Expo Router and Clerk authentication.
Dependencies are managed by the root pnpm workspace.

## Development

Follow the root [development guide](../../docs/DEVELOPMENT.md), then run:

```bash
pnpm my-expo-app start
```

[`app.config.js`](app.config.js) reads the root `.env` and requires
`EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CUSTOM_RAILWAY_BACKEND_URL`.
A physical device needs a reachable LAN or tunnel backend URL; `localhost` points
to the device itself. Cloud builds use the selected EAS environment.

Routes live in `app/`; notification registration is in `utils/notifications.ts`.
On sign-in, the client requests an Expo push token and registers it with the API
using a Clerk session token. The backend associates it with the authenticated
user and sends notifications through Expo's push service. See
[`api/src/push/`](../../api/src/push/) for storage and delivery.

## Native builds

Run EAS commands from this directory. Profiles are defined in [eas.json](eas.json):

```bash
eas build --profile local --local --platform ios
eas build --profile development --platform ios
eas build --profile production --platform ios
```

Local iOS builds require Xcode, CocoaPods, and Fastlane. Android builds use
`--platform android` and require the Android toolchain for local builds.
See [Expo build documentation](https://docs.expo.dev/build/introduction/)
for platform setup and store submission.
