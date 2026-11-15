import { isRouteErrorResponse, NavLink } from "react-router";

import { SERNIA_CONTACT_EMAIL, SERNIA_NAME } from "~/lib/sernia-public-seo";
import { cn } from "~/lib/utils";

/**
 * Header and footer for Sernia Capital's public pages. Nothing from the
 * personal site (sidebar, navigation, branding) appears here.
 */
export function SerniaPublicShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-[calc(100dvh-var(--env-banner-h,0px))] flex-col bg-background text-foreground">
      <header className="border-b">
        <div className="mx-auto flex h-14 w-full max-w-3xl items-center px-4 sm:px-6">
          <p className="text-base font-semibold tracking-tight">{SERNIA_NAME}</p>
        </div>
      </header>
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10 sm:px-6 sm:py-14">{children}</main>
      <footer className="border-t">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-2 px-4 py-6 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <p>Equal Housing Opportunity</p>
          <nav aria-label="Policies" className="flex gap-5">
            <FooterLink to="/privacy">Privacy</FooterLink>
            <FooterLink to="/sms-terms">SMS Terms</FooterLink>
          </nav>
        </div>
      </footer>
    </div>
  );
}

function FooterLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "inline-block py-1 underline-offset-4 hover:text-foreground hover:underline",
          isActive && "text-foreground",
        )
      }
    >
      {children}
    </NavLink>
  );
}

/** Body of an error page under the Sernia layout. */
export function SerniaPublicError({ error }: { error: unknown }) {
  const notFound = isRouteErrorResponse(error) && error.status === 404;
  const stack = import.meta.env.DEV && error instanceof Error ? error.stack : undefined;

  return (
    <div className="space-y-3">
      <h1 className="text-2xl font-semibold tracking-tight">
        {notFound ? "Page not found" : "Something went wrong"}
      </h1>
      <p className="leading-7 text-muted-foreground">
        {notFound
          ? "There is no page at this address."
          : "This page could not be shown. Please try again in a moment."}{" "}
        Questions? Email{" "}
        <a href={`mailto:${SERNIA_CONTACT_EMAIL}`} className="font-medium text-foreground underline underline-offset-4">
          {SERNIA_CONTACT_EMAIL}
        </a>
        .
      </p>
      {stack && (
        <pre className="overflow-x-auto rounded-md border p-4 text-xs">
          <code>{stack}</code>
        </pre>
      )}
    </div>
  );
}
