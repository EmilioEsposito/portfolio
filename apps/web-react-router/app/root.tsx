import {
  isRouteErrorResponse,
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
  useLocation,
  useRouteError,
  useRouteLoaderData,
  type ShouldRevalidateFunctionArgs,
} from "react-router";
import {
  ClerkProvider,
} from "@clerk/react-router";
import { clerkMiddleware, rootAuthLoader } from "@clerk/react-router/server";
import { ThemeProvider } from "next-themes";

import type { Route } from "./+types/root";
import "./app.css";
import { SITE_OWNER, DEFAULT_META } from "~/sites/portfolio/lib/seo";
import { Navbar } from "~/sites/portfolio/components/navbar";
import { SidebarProvider, SidebarInset } from "~/components/ui/sidebar";
import { AppSidebar } from "~/sites/portfolio/components/app-sidebar";
import { Toaster } from "~/components/ui/toaster";
import { cn } from "~/lib/utils";
import { EnvBanner } from "~/components/env-banner";
import { SerniaPublicError, SerniaPublicShell } from "~/sites/sernia-public/components/shell";
import { createRootMiddleware, loadRoot } from "~/sites/root-request.server";
import { isSerniaPublicPath } from "~/sites/site";
import { SERNIA_NAME, serniaErrorMeta } from "~/sites/sernia-public/lib/seo";
import { serniaRoot } from "~/sites/sernia-root";

// Sernia Capital's public pages (serniacapital.com, and their paths on any
// host while they are switched on) load no Clerk at all and use their own
// layout, icons and font. ~/sites/site decides which requests those
// are; the server tells the browser through the root loader data or the 404 it
// throws, which ~/sites/sernia-root reads.

export function meta({ error, loaderData }: Route.MetaArgs) {
  if (serniaRoot(error, loaderData)) {
    if (error) return serniaErrorMeta(isRouteErrorResponse(error) ? error.status : undefined);
    // Sernia pages set their own meta; this keeps the personal site's out if one does not.
    return [{ title: SERNIA_NAME }];
  }
  return [
    { title: SITE_OWNER },
    { name: "description", content: DEFAULT_META.description },
    { property: "og:title", content: SITE_OWNER },
    { property: "og:description", content: DEFAULT_META.description },
    { property: "og:image", content: DEFAULT_META.image },
    { property: "og:type", content: "website" },
    { name: "twitter:card", content: "summary_large_image" },
  ];
}

export const middleware: Route.MiddlewareFunction[] = [createRootMiddleware(clerkMiddleware())];

export const loader = (args: Route.LoaderArgs) =>
  loadRoot(args, () =>
    rootAuthLoader(args, () => ({
      // Railway sets this for every deployed environment. Passing it through the
      // root loader makes banner behavior independent of whichever custom domain
      // happens to route to that deployment.
      railwayEnvironmentName: process.env.RAILWAY_ENVIRONMENT_NAME,
    })),
  );

export function shouldRevalidate({
  currentUrl,
  nextUrl,
  defaultShouldRevalidate,
}: ShouldRevalidateFunctionArgs) {
  // Moving between a Sernia public path and the rest of the app can switch the
  // root between its Clerk and no-Clerk shapes, and only the server knows
  // whether the pages are switched on, so its loader has to run.
  if (isSerniaPublicPath(currentUrl.pathname) !== isSerniaPublicPath(nextUrl.pathname)) return true;
  return defaultShouldRevalidate;
}

export const links: Route.LinksFunction = () => [
  { rel: "preconnect", href: "https://fonts.googleapis.com" },
  {
    rel: "preconnect",
    href: "https://fonts.gstatic.com",
    crossOrigin: "anonymous",
  },
];

/** The app's icons, install manifest and font. Not in `links`, which every page gets. */
function AppLinks() {
  return (
    <>
      <link rel="manifest" href="/manifest.json" />
      <link rel="icon" href="/favicon.png" type="image/png" />
      <link rel="apple-touch-icon" href="/favicon.png" />
      <link
        rel="stylesheet"
        href="https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap"
      />
    </>
  );
}

/** The Sernia public pages' icons and font. The app's manifest opens /sernia-chat, so it stays out. */
function SerniaLinks() {
  return (
    <>
      <link rel="icon" href="/images/sernia/icon.png" type="image/png" sizes="96x96" />
      <link rel="icon" href="/images/sernia/icon.svg" type="image/svg+xml" sizes="any" />
      <link rel="apple-touch-icon" href="/images/sernia/apple-touch-icon.png" />
      <link
        rel="stylesheet"
        href="https://fonts.googleapis.com/css2?family=Figtree:wght@400..700&display=swap"
      />
    </>
  );
}

export function Layout({ children }: { children: React.ReactNode }) {
  const sernia = serniaRoot(useRouteError(), useRouteLoaderData("root"));
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="default" />
        <meta name="apple-mobile-web-app-title" content="Sernia" />
        <Meta />
        <Links />
        {sernia ? <SerniaLinks /> : <AppLinks />}
      </head>
      <body className={cn("antialiased overflow-x-hidden")}>
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

const SERNIA_ROUTE_PREFIXES = ["/sernia-chat", "/sernia-admin", "/sernia-settings"];

function isSerniaRoute(pathname: string) {
  return SERNIA_ROUTE_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

export default function App({ loaderData }: Route.ComponentProps) {
  const location = useLocation();

  if ("serniaPublic" in loaderData) {
    // No ClerkProvider, so no Clerk script or request. The Sernia layout
    // route supplies the header and footer.
    return (
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        <Toaster />
        <EnvBanner railwayEnvironmentName={loaderData.railwayEnvironmentName} />
        <Outlet />
      </ThemeProvider>
    );
  }

  const isSernia = isSerniaRoute(location.pathname);

  return (
    <ClerkProvider loaderData={loaderData}>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        <Toaster />
        {isSernia ? (
          // Sernia routes provide their own sidebar — render Outlet directly
          <>
            <EnvBanner railwayEnvironmentName={loaderData.railwayEnvironmentName} />
            <Outlet />
          </>
        ) : (
          <SidebarProvider>
            <AppSidebar />
            <SidebarInset className="min-w-0 overflow-x-clip">
              <Navbar />
              <EnvBanner railwayEnvironmentName={loaderData.railwayEnvironmentName} />
              <Outlet />
            </SidebarInset>
          </SidebarProvider>
        )}
      </ThemeProvider>
    </ClerkProvider>
  );
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  const rootData = useRouteLoaderData("root");
  if (serniaRoot(error, rootData)) {
    return (
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        <SerniaPublicShell>
          <SerniaPublicError error={error} />
        </SerniaPublicShell>
      </ThemeProvider>
    );
  }

  let message = "Oops!";
  let details = "An unexpected error occurred.";
  let stack: string | undefined;

  if (isRouteErrorResponse(error)) {
    message = error.status === 404 ? "404" : "Error";
    details =
      error.status === 404
        ? "The requested page could not be found."
        : error.statusText || details;
  } else if (import.meta.env.DEV && error && error instanceof Error) {
    details = error.message;
    stack = error.stack;
  }

  return (
    <main className="pt-16 p-4 container mx-auto">
      <h1>{message}</h1>
      <p>{details}</p>
      {stack && (
        <pre className="w-full p-4 overflow-x-auto">
          <code>{stack}</code>
        </pre>
      )}
    </main>
  );
}
