import { Loader2 } from "lucide-react";

import { Button } from "~/components/ui/button";
import { TeamWordmark } from "~/sites/team/components/team-wordmark";
import { TEAM_EMAIL_DOMAIN } from "~/sites/team/lib/team-access";

// What the team's pages show instead of themselves: while sign-in is being
// checked, to someone signed out, and to an account that is not the team's.
// No Clerk here, so tests can render them; team-guard.tsx wires them to Clerk.

function GatePage({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-[calc(100dvh-var(--env-banner-h,0px))] items-center justify-center bg-background px-4 py-10">
      <div className="flex w-full max-w-sm flex-col items-center gap-5 text-center">
        <TeamWordmark className="text-xl [&_svg]:size-10" />
        {children}
      </div>
    </main>
  );
}

export function TeamChecking() {
  return (
    <GatePage>
      <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden="true" />
      <p className="sr-only" role="status">Checking sign-in…</p>
    </GatePage>
  );
}

export function TeamSignIn({
  onSignIn,
  opening,
  failed,
}: {
  onSignIn: () => void;
  /** Sign-in was pressed and its window is on its way. */
  opening: boolean;
  /** Sign-in has not loaded after a while. */
  failed: boolean;
}) {
  return (
    <GatePage>
      <h1 className="text-base text-muted-foreground">This area is for the Sernia Capital team.</h1>
      <Button size="lg" onClick={onSignIn} disabled={opening && !failed} className="min-w-36 gap-2">
        {opening && !failed && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
        {opening && !failed ? "Opening sign-in…" : "Sign in"}
      </Button>
      {failed && (
        <p role="alert" className="text-sm text-muted-foreground">
          Sign-in could not load. Check your connection and reload the page.
        </p>
      )}
    </GatePage>
  );
}

export function TeamWrongAccount({
  address,
  onSignOut,
  signingOut,
}: {
  address: string | null;
  onSignOut: () => void;
  signingOut: boolean;
}) {
  return (
    <GatePage>
      <div className="space-y-2">
        <h1 className="text-base font-medium">This account is not on the Sernia Capital team.</h1>
        <p className="text-sm leading-6 text-muted-foreground">
          {address ? <>You are signed in as {address}. </> : null}
          Sign in with a verified @{TEAM_EMAIL_DOMAIN} address to use these pages.
        </p>
      </div>
      <Button size="lg" variant="outline" onClick={onSignOut} disabled={signingOut} className="min-w-36 gap-2">
        {signingOut && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
        {signingOut ? "Signing out…" : "Sign out"}
      </Button>
    </GatePage>
  );
}
