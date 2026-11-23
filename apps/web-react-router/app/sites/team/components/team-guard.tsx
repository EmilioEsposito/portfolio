import { useEffect, useState } from "react";
import { useAuth, useClerk, useUser } from "@clerk/react-router";
import { useLocation } from "react-router";

import { TeamChecking, TeamSignIn, TeamWrongAccount } from "~/sites/team/components/team-gate";
import { teamAccess, teamEmails } from "~/sites/team/lib/team-access";

/** How long sign-in may take to load after it is pressed before the page says so. */
const SIGN_IN_LOAD_LIMIT_MS = 15_000;

/**
 * The one guard in front of every team page: a signed-in user with a verified
 * @serniacapital.com address gets in. The API enforces access on its own;
 * this is what the browser shows.
 *
 * The server renders the signed-out page from Clerk's state in the root
 * loader, before Clerk's script has loaded in the browser.
 */
export function TeamGuard({ banner, children }: { banner?: React.ReactNode; children: React.ReactNode }) {
  const { isLoaded, isSignedIn } = useAuth();
  const { user } = useUser();
  const access = teamAccess({ authLoaded: isLoaded, signedIn: isSignedIn, emails: teamEmails(user) });
  if (access === "allowed") return <>{children}</>;
  return (
    <>
      {banner}
      {access === "loading" && <TeamChecking />}
      {access === "signed-out" && <SignIn />}
      {access === "wrong-account" && <WrongAccount address={user?.primaryEmailAddress?.emailAddress ?? null} />}
    </>
  );
}

function SignIn() {
  const clerk = useClerk();
  const { isLoaded: clerkLoaded } = useUser();
  const location = useLocation();
  const [opening, setOpening] = useState(false);
  const [failed, setFailed] = useState(false);

  // Clerk opens the window itself once its script has loaded.
  useEffect(() => {
    if (opening && clerkLoaded) setOpening(false);
  }, [opening, clerkLoaded]);
  useEffect(() => {
    if (!opening) return;
    const timer = setTimeout(() => setFailed(true), SIGN_IN_LOAD_LIMIT_MS);
    return () => clearTimeout(timer);
  }, [opening]);

  return (
    <TeamSignIn
      opening={opening}
      failed={failed}
      onSignIn={() => {
        setFailed(false);
        setOpening(!clerkLoaded);
        // Clerk keeps this until its script loads, then opens the sign-in window.
        clerk.openSignIn({ forceRedirectUrl: location.pathname + location.search });
      }}
    />
  );
}

function WrongAccount({ address }: { address: string | null }) {
  const clerk = useClerk();
  const location = useLocation();
  const [signingOut, setSigningOut] = useState(false);
  return (
    <TeamWrongAccount
      address={address}
      signingOut={signingOut}
      onSignOut={() => {
        setSigningOut(true);
        void clerk
          .signOut({ redirectUrl: location.pathname + location.search })
          .catch(() => setSigningOut(false));
      }}
    />
  );
}
