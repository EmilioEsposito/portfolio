import { useEffect } from "react";
import {
  isRouteErrorResponse,
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
  useRouteError,
  useRouteLoaderData,
  type ShouldRevalidateFunctionArgs,
} from "react-router";
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
import { retireRootServiceWorkerOnce } from "~/lib/retire-root-service-worker";
import { SerniaPublicError, SerniaPublicShell } from "~/sites/sernia-public/components/shell";
import { TeamError } from "~/sites/team/components/team-error";
import { teamTitle } from "~/sites/team/lib/team-meta";
import { createRootMiddleware, loadRoot } from "~/sites/root-request.server";
import { isSerniaPublicPath, isTeamPath } from "~/sites/site";
import { SERNIA_NAME, serniaErrorMeta } from "~/sites/sernia-public/lib/seo";
import { rootSite, serniaRoot } from "~/sites/sernia-root";

// Three sites share this root (~/sites/site decides which one a request is):
// the portfolio, Sernia Capital's public pages and the Sernia team's pages
// under /team. Only the team's pages run Clerk: its middleware and its root
// loader here, on the server, and <ClerkProvider> in the team's layout. The
// portfolio and the Sernia public pages download no Clerk code, load no Clerk
// script and make no Clerk request. The server tells the browser
// which site a page is through the root loader data (`site`) or the Sernia
// 404 it throws, which ~/sites/sernia-root reads.

export function meta({ error, loaderData }: Route.MetaArgs) {
  if (serniaRoot(error, loaderData)) {
    if (error) return serniaErrorMeta(isRouteErrorResponse(error) ? error.status : undefined);
    // Sernia pages set their own meta; this keeps the personal site's out if one does not.
    return [{ title: SERNIA_NAME }];
  }
  if (rootSite(loaderData) === "team") {
    // Team pages set their own title; this is for the team's error page.
    const notFound = isRouteErrorResponse(error) && error.status === 404;
    return [{ title: teamTitle(error ? (notFound ? "Page not found" : "Something went wrong") : undefined) }];
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

// Railway sets RAILWAY_ENVIRONMENT_NAME for every deployed environment. The
// root loader passes it on, so the banner does not depend on which custom
// domain reached the deployment.
export const loader = (args: Route.LoaderArgs) =>
  loadRoot(args, (team) => rootAuthLoader(args, () => team));

export function shouldRevalidate({
  currentUrl,
  nextUrl,
  defaultShouldRevalidate,
}: ShouldRevalidateFunctionArgs) {
  // Moving between sites switches the root between its shapes (only the team
  // runs Clerk), and only the server knows whether the Sernia public pages are
  // switched on, so its loader has to run.
  if (isSerniaPublicPath(currentUrl.pathname) !== isSerniaPublicPath(nextUrl.pathname)) return true;
  if (isTeamPath(currentUrl.pathname) !== isTeamPath(nextUrl.pathname)) return true;
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

const INTER =
  "https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap";

/** The portfolio's icons and font. Not in `links`, which every page gets. */
function PortfolioHead() {
  return (
    <>
      <link rel="icon" href="/favicon.png" type="image/png" />
      <link rel="apple-touch-icon" href="/favicon.png" />
      <link rel="stylesheet" href={INTER} />
    </>
  );
}

/** Sernia's icons, which the public pages and the team's share. */
function SerniaIcons() {
  return (
    <>
      <link rel="icon" href="/images/sernia/icon.png" type="image/png" sizes="96x96" />
      <link rel="icon" href="/images/sernia/icon.svg" type="image/svg+xml" sizes="any" />
      <link rel="apple-touch-icon" href="/images/sernia/apple-touch-icon.png" />
    </>
  );
}

/** The Sernia public pages' icons and font. */
function SerniaPublicHead() {
  return (
    <>
      <SerniaIcons />
      <link
        rel="stylesheet"
        href="https://fonts.googleapis.com/css2?family=Figtree:wght@400..700&display=swap"
      />
    </>
  );
}

/**
 * The team's pages: Sernia's icons, the app's font, the install manifest and
 * the home-screen app tags (an installed app opens /team/chat), and no
 * search engines.
 */
function TeamHead() {
  return (
    <>
      <meta name="robots" content="noindex, nofollow" />
      <meta name="apple-mobile-web-app-capable" content="yes" />
      <meta name="apple-mobile-web-app-status-bar-style" content="default" />
      <meta name="apple-mobile-web-app-title" content="Sernia" />
      <link rel="manifest" href="/team/manifest.json" />
      <SerniaIcons />
      <link rel="stylesheet" href={INTER} />
    </>
  );
}

export function Layout({ children }: { children: React.ReactNode }) {
  const error = useRouteError();
  const rootData = useRouteLoaderData("root");
  const sernia = serniaRoot(error, rootData);
  const team = !sernia && rootSite(rootData) === "team";
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover" />
        <Meta />
        <Links />
        {sernia ? <SerniaPublicHead /> : team ? <TeamHead /> : <PortfolioHead />}
      </head>
      <body className={cn("antialiased overflow-x-hidden")}>
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

export default function App({ loaderData }: Route.ComponentProps) {
  switch (loaderData.site) {
    case "team":
      // The team layout (app/sites/team/routes/layout.tsx) supplies Clerk's
      // provider, the guard, the navigation and the banner. The provider is
      // there, not here, so only the team's pages download Clerk's code.
      return (
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <Toaster />
          <Outlet />
        </ThemeProvider>
      );
    case "sernia-public":
      // The Sernia layout route supplies the header and footer.
      return (
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <Toaster />
          <EnvBanner railwayEnvironmentName={loaderData.railwayEnvironmentName} />
          <Outlet />
        </ThemeProvider>
      );
    case "portfolio":
      return (
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <RetireStaffServiceWorker />
          <Toaster />
          <SidebarProvider>
            <AppSidebar />
            <SidebarInset className="min-w-0 overflow-x-clip">
              <Navbar />
              <EnvBanner railwayEnvironmentName={loaderData.railwayEnvironmentName} />
              <Outlet />
            </SidebarInset>
          </SidebarProvider>
        </ThemeProvider>
      );
  }
}

/** Removes the service worker the staff app left here before it moved to /team. */
function RetireStaffServiceWorker() {
  useEffect(retireRootServiceWorkerOnce, []);
  return null;
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
  if (rootSite(rootData) === "team") {
    return (
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        <TeamError error={error} />
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
