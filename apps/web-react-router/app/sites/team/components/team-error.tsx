import { isRouteErrorResponse } from "react-router";

import { TeamWordmark } from "~/sites/team/components/team-wordmark";

/**
 * The root's error page for a team page: an unknown /team/... address, or a
 * page that failed. The root renders it without Clerk, so it uses none.
 */
export function TeamError({ error }: { error: unknown }) {
  const notFound = isRouteErrorResponse(error) && error.status === 404;
  const stack = import.meta.env.DEV && error instanceof Error ? error.stack : undefined;
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-xl flex-col justify-center gap-5 px-4 py-10">
      <TeamWordmark className="text-lg" />
      <h1 className="text-2xl font-semibold tracking-tight">{notFound ? "Page not found" : "Something went wrong"}</h1>
      <p className="leading-7 text-muted-foreground">
        {notFound ? "There is no team page at this address." : "This page could not be shown. Try again in a moment."}
      </p>
      <p>
        {/* A full page load, so the team home starts from a fresh root. */}
        <a href="/team" className="font-medium underline underline-offset-4">
          Go to the team home
        </a>
      </p>
      {stack && (
        <pre className="overflow-x-auto rounded-md border p-4 text-xs">
          <code>{stack}</code>
        </pre>
      )}
    </main>
  );
}
