import { isRouteErrorResponse, Link, NavLink, useNavigation, useRouteError, useRouteLoaderData } from "react-router";

import { LogoMark } from "~/components/sernia-logo";
import { SERNIA_CONTACT_EMAIL, SERNIA_NAME } from "~/sites/sernia-public/lib/seo";
import { serniaRoot } from "~/sites/sernia-root";
import { cn } from "~/lib/utils";

const COLUMN = "mx-auto w-full max-w-3xl px-4 sm:px-6";
/** The home page's column, for its sections inside the shell. */
export const SERNIA_WIDE_COLUMN = "mx-auto w-full max-w-[1080px] px-[18px] sm:px-6";

const FOOTER_LINK = "inline-block py-1 underline underline-offset-[3px] hover:text-foreground";

/**
 * Header and footer for Sernia Capital's public pages. Nothing from the
 * personal site (sidebar, navigation, branding) appears here. Its
 * `sernia-public` class carries the brand colors and font (app.css).
 */
export function SerniaPublicShell({
  children,
  wide = false,
  action,
}: {
  children: React.ReactNode;
  /** For the home page: a wider column, and the page lays itself out from edge to edge. */
  wide?: boolean;
  /** At the end of the header, across from the wordmark. */
  action?: React.ReactNode;
}) {
  const column = wide ? SERNIA_WIDE_COLUMN : COLUMN;
  const teamHref = serniaRoot(useRouteError(), useRouteLoaderData("root"))?.teamHref ?? "/team";
  return (
    <div className="sernia-public flex min-h-[calc(100dvh-var(--env-banner-h,0px))] flex-col bg-background text-foreground">
      <OpeningLine />
      <header className="border-b">
        <div
          className={cn(column, "flex min-h-16 flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2 sm:min-h-[72px]")}
        >
          <Wordmark />
          <div className="flex items-center gap-x-5">
            {/* From a phone's width up; below it the header keeps to one line and the footer has the link. */}
            <TeamLink href={teamHref} className="hidden py-2 text-[0.93rem] text-muted-foreground sm:inline-block" />
            {action}
          </div>
        </div>
      </header>
      <main className={wide ? "flex-1" : cn(COLUMN, "flex-1 py-10 sm:py-14")}>{children}</main>
      <footer className="border-t">
        <div className={cn(column, "flex flex-wrap items-center gap-x-7 gap-y-1 py-6 text-[0.93rem] text-muted-foreground")}>
          <p className="py-1">{SERNIA_NAME}, Pittsburgh, PA</p>
          <p className="py-1">Equal Housing Opportunity</p>
          <div className="flex gap-5 md:ml-auto">
            <a href={`mailto:${SERNIA_CONTACT_EMAIL}`} className={FOOTER_LINK}>
              Email us
            </a>
            <nav aria-label="Policies" className="flex gap-5">
              <FooterLink to="/privacy">Privacy</FooterLink>
              {/* The booking form and the privacy policy link to these too; the home page has no need to. */}
              {!wide && <FooterLink to="/sms-terms">SMS Terms</FooterLink>}
            </nav>
            <TeamLink href={teamHref} className={FOOTER_LINK} />
          </div>
        </div>
      </footer>
    </div>
  );
}

/** A line across the top from the moment a link is clicked until its page opens. */
function OpeningLine() {
  if (useNavigation().state === "idle") return null;
  return (
    <div
      role="progressbar"
      aria-label="Opening the page"
      className="sernia-opening fixed inset-x-0 top-0 z-50 h-[3px] origin-left bg-sernia-sky"
    />
  );
}

/** The logo and name, a link to the home page on the hosts where `/` is it. */
function Wordmark() {
  const home = serniaRoot(useRouteError(), useRouteLoaderData("root"))?.homeAtRoot ?? false;
  const className = "flex items-center gap-2.5 text-[1.05rem] font-bold sm:gap-3 sm:text-[1.15rem]";
  const content = (
    <>
      <LogoMark className="size-[34px] shrink-0 sm:size-[38px]" />
      {SERNIA_NAME}
    </>
  );
  return home ? (
    <Link
      to="/"
      className={cn(
        className,
        "rounded-sm focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring",
      )}
    >
      {content}
    </Link>
  ) : (
    <p className={className}>{content}</p>
  );
}

/**
 * The way to the team's signed-in pages. A full page load, since they run
 * Clerk and these pages do not.
 */
function TeamLink({ href, className }: { href: string; className?: string }) {
  return (
    <a href={href} className={cn("hover:text-foreground", className)}>
      Team
    </a>
  );
}

function FooterLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink to={to} className={({ isActive }) => cn(FOOTER_LINK, isActive && "text-foreground")}>
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
