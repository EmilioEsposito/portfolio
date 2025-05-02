# Legend
🚧 indicate the current in progress epics/tasks
⏸️ indicate the paused epics/tasks
✅ indicate the completed epics/tasks
[ ] indicate the epics/tasks that are not started yet

# Epics Table of Contents
- [⏸️] [Migrate from Vercel to Railway](roadmap.md#migrate-from-vercel-to-railway)
- [🚧] [Incorporate React Native](roadmap.md#incorporate-react-native)
- [ ] Create AP Scheduler Service inside of FastAPI app (w/ jobs and job-runs saved to Postgres tables)
- [ ] Augment FastAPI Chat with custom Tools Usage Functions (sending/receiving emails/texts)


# Epics
## Migrate from Vercel to Railway

- [✅] Dockerize the FastAPI and NextJS apps
- [✅] Get it running on Railway
- [✅] Remove Vercel specific code
- [✅] Changed domains to be Cloudflare managed and added Railway settings
- [] Finalize domain transfer to Cloudflare billing - Not Urgent. Due Date [2025-05-01]

## Incorporate React Native

### AI Chat Context
[Emilio]: I want to incorporate React Native into the application as well. I want to keep NextJS, which I'll need for things like SSR SEO pages. However, my ideal state is that I can have some pages written in ReactNative that are rendered on both native apps, but can also be accessed in ReactNativeWeb as well (which I *think* can be hosted inside of NextJS pages?). I don't want to do anything hacky, so let me know best practices here. Also, I heard good things about Expo? Help me think through how to go about this and how to struture it. 

[AI]: Okay, let's break down how to integrate React Native into your existing setup while keeping Next.js and leveraging React Native for Web (RNW). This is a common goal, and there are established patterns to achieve it cleanly.
1. Expo: Highly Recommended
You heard right about Expo. For your use case, using Expo is highly recommended:
Managed Workflow: Expo simplifies the setup, build, and update process for React Native apps, abstracting away much of the native tooling complexity for iOS and Android.
Cross-Platform Tools: It provides a suite of libraries and APIs (Expo SDK) designed to work seamlessly across web, iOS, and Android.
React Native for Web Integration: Expo has first-class support for RNW. The @expo/next-adapter package makes integrating your Expo (React Native) code into a Next.js project relatively straightforward.
Using Expo is generally considered a best practice for starting new React Native projects, especially when targeting the web as well.
2. React Native for Web (RNW)
RNW is a library that allows you to run your React Native components and APIs directly in a web browser. It essentially translates React Native primitives (View, Text, Image, etc.) into their web equivalents (div, span, img, etc.). This is the key to sharing code between your native apps and your web app.
3. Integrating RNW with Next.js (via Expo)
Instead of "hosting RNW inside Next.js," think of it as building your React Native components as part of your Next.js application.
You'll use Expo's @expo/next-adapter. This adapter modifies your Next.js configuration (next.config.js) to:
Correctly transpile React Native and Expo libraries for the web.
Alias react-native to react-native-web.
Set up styles (Expo uses its own styling solution that works cross-platform).
Your React Native components written for the Expo app can then be imported and rendered directly within your Next.js pages or components. Next.js's SSR/SSG capabilities will still work for the pages themselves, and the RNW components will render as standard web elements within those pages.
4. Recommended Project Structure: Monorepo
The standard and cleanest way to manage this kind of shared codebase is using a monorepo structure, likely with pnpm workspaces (which you're already using).

```text 
/portfolio (repo root)
├── api/          # Your existing FastAPI app (can stay here for now since it won't share code with the other apps, and makes it easier given existing structure)
│   ├── requirements.txt
│   ├── railway_fastapi.json
│   ├── Dockerfile
│   └── etc...
├── apps/
│   ├── web/          # Your existing Next.js app (might need renaming/moving)
│   │   ├── package.json
│   │   ├── railway_nextjs.json
│   │   ├── Dockerfile
│   │   └── etc...
│   ├── my-expo-app/       # Your new Expo (React Native) app
│   │   ├── <tbd>
│   │   ├── <tbd>
│   │   └── etc...
├── packages/
│   └── etc...
├── venv/
├── docker-compose.yaml
├── package.json     # Root package.json configuring pnpm workspaces
├── pnpm-workspace.yaml # Defines workspace locations
├── pnpm-lock.yaml 
├── .env.development.local
├── README.md
├── roadmap.md
└── ...
```

[Emilio]: Ok, this sounds good. Let's break this up into stages so that we can test for breakages and fix them after each stage, which is easier than trying to do every single change then fixing things after. E.g. maybe let's first just try some of the restructuring before even introducing any RN/expo code?

### Tasks
- [✅] Move NextJS app under /apps/web to enable a sort of monorepo structure since NextJS and React Native can share code.  
- [✅]Revisit the overall plan and update/reorder draft tasks on this roadmap as needed, then pick the next task to work on and move it up here.. 
- [✅] Initialize Expo App in `/apps/my-expo-app`. Use SDK 53 so that it is compatible with React 19. 
- [✅] Revisit the overall plan and update/reorder draft tasks on this roadmap as needed, then pick the next task to work on and move it up here.
- [✅] Configure Monorepo for Shared UI: Set up packages/ui (Confirm `pnpm-workspace.yaml`, create `packages/ui` directory and `package.json`. Decision: Keep Expo default components in `apps/my-expo-app/components`).
- [✅] Integrate Expo with Next.js (Install and configure `@expo/next-adapter` in `apps/web`). 

- [✅] Create a basic "hello world" shared screen in `packages`? 
- [✅] Test integration by rendering the shared screen in both `apps/my-expo-app` and `apps/web`.
- [✅] Get Clerk basic auth working in Expo app.
- [ ] Test Expo app in iOS/Android somehow. Are there emulators/simulators? And then what about physical devices?
- [ ] Add Clerk Google SSO to Expo app.
- [ ] Revisit the overall plan and update/reorder draft tasks on this roadmap as needed, then pick the next task to work on and move it up here.


### Draft Tasks (not necessarily in this order)
- [ ] Get "hello world" level push notifications working on Expo app.
- [ ] MAYBE: Recreate apps/web/app/message-tenants/page.tsx in React Native, then have it render in both `apps/my-expo-app` and `apps/web`. If necessary, maybe use some of components from the new [Expo UI](https://docs.expo.dev/versions/v53.0.0/sdk/ui/) library in the example app just to test? 
- [ ] MAYBE: Make frontend chat interface compatible with both NextJS and React Native.
