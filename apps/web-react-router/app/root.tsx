import {
  data,
  isRouteErrorResponse,
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
  useLocation,
  type ShouldRevalidateFunctionArgs,
} from "react-router";
import {
  ClerkProvider,
} from "@clerk/react-router";
import { clerkMiddleware, rootAuthLoader } from "@clerk/react-router/server";
import { ThemeProvider } from "next-themes";

import type { Route } from "./+types/root";
import "./app.css";
import { SITE_OWNER, DEFAULT_META } from "~/lib/seo";
import { Navbar } from "~/components/navbar";
import { SidebarProvider, SidebarInset } from "~/components/ui/sidebar";
import { AppSidebar } from "~/components/app-sidebar";
import { Toaster } from "~/components/ui/toaster";
import { cn } from "~/lib/utils";
import { EnvBanner } from "~/components/env-banner";
import { SerniaPublicError, SerniaPublicShell } from "~/components/sernia-public/shell";
import {
  isSerniaPublicPath,
  isSerniaPublicRequest,
  rootRequestMode,
  SERNIA_NOT_FOUND,
} from "~/lib/sernia-public";
import { SERNIA_NAME, serniaErrorMeta } from "~/lib/sernia-public-seo";

// Sernia Capital's public pages (serniacapital.com, and their paths on any
// host) load no Clerk at all and use their own layout. ~/lib/sernia-public
// decides which requests those are.

function isSerniaNotFound(error: unknown) {
  return isRouteErrorResponse(error) && error.data === SERNIA_NOT_FOUND;
}

/** Errors on a Sernia host or a Sernia public path render under the Sernia layout. */
function isSerniaPublicError(error: unknown, pathname: string) {
  return isSerniaNotFound(error) || isSerniaPublicPath(pathname);
}

export function meta({ error, location }: Route.MetaArgs) {
  if (isSerniaPublicError(error, location.pathname)) {
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

const clerk = clerkMiddleware();

export const middleware: Route.MiddlewareFunction[] = [
  (args, next) => {
    switch (rootRequestMode(args.url)) {
      case "sernia-not-found":
        // A Sernia host answers only the Sernia public pages.
        throw data(SERNIA_NOT_FOUND, { status: 404, statusText: "Not Found" });
      case "sernia-public":
        return next();
      case "app":
        return clerk(args, next);
    }
  },
];

export const loader = (args: Route.LoaderArgs) => {
  if (isSerniaPublicRequest(args.url)) {
    // The middleware skipped Clerk, so rootAuthLoader cannot run here.
    return { serniaPublic: true as const, railwayEnvironmentName: process.env.RAILWAY_ENVIRONMENT_NAME };
  }
  return rootAuthLoader(args, () => ({
    // Railway sets this for every deployed environment. Passing it through the
    // root loader makes banner behavior independent of whichever custom domain
    // happens to route to that deployment.
    railwayEnvironmentName: process.env.RAILWAY_ENVIRONMENT_NAME,
  }));
};

export function shouldRevalidate({
  currentUrl,
  nextUrl,
  defaultShouldRevalidate,
}: ShouldRevalidateFunctionArgs) {
  // Moving between a Sernia public page and the rest of the app switches the
  // root between its Clerk and no-Clerk shapes, so its loader has to run.
  if (isSerniaPublicPath(currentUrl.pathname) !== isSerniaPublicPath(nextUrl.pathname)) return true;
  return defaultShouldRevalidate;
}

export const links: Route.LinksFunction = () => [
  { rel: "manifest", href: "/manifest.json" },
  { rel: "icon", href: "/favicon.png", type: "image/png" },
  { rel: "apple-touch-icon", href: "/favicon.png" },
  { rel: "preconnect", href: "https://fonts.googleapis.com" },
  {
    rel: "preconnect",
    href: "https://fonts.gstatic.com",
    crossOrigin: "anonymous",
  },
  {
    rel: "stylesheet",
    href: "https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap",
  },
];

export function Layout({ children }: { children: React.ReactNode }) {
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
  const location = useLocation();
  if (isSerniaPublicError(error, location.pathname)) {
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
