// Who the team's pages let in. The API checks every request on its own; this
// decides what the browser shows. Kept free of Clerk so tests can run it.

/** The email domain a team member signs in with. */
export const TEAM_EMAIL_DOMAIN = "serniacapital.com";

/** The account that also sees the general agent's settings and every conversation. */
const ADMIN_EMAIL = "emilio@serniacapital.com";

export type TeamEmail = { address: string; verified: boolean };

/**
 * - `loading`: Clerk does not know yet who is signed in.
 * - `signed-out`: no one is signed in.
 * - `wrong-account`: signed in, without a verified @serniacapital.com address.
 * - `allowed`: a team member.
 */
export type TeamAccess = "loading" | "signed-out" | "wrong-account" | "allowed";

export function teamAccess(state: {
  /** useAuth().isLoaded */
  authLoaded: boolean;
  /** useAuth().isSignedIn */
  signedIn: boolean | undefined;
  /** The signed-in user's addresses, or undefined until the user has loaded. */
  emails: TeamEmail[] | undefined;
}): TeamAccess {
  if (!state.authLoaded) return "loading";
  if (!state.signedIn) return "signed-out";
  if (!state.emails) return "loading";
  return state.emails.some((email) => email.verified && isTeamAddress(email.address)) ? "allowed" : "wrong-account";
}

/**
 * Whether this browser's push subscription must be ended: once Clerk knows
 * that no team member is signed in here. Notifications carry reply previews
 * and approval details, so they stop with the sign-in. Never while loading.
 */
export function endsPush(access: TeamAccess): boolean {
  return access === "signed-out" || access === "wrong-account";
}

export function isTeamAddress(address: string): boolean {
  return address.trim().toLowerCase().endsWith(`@${TEAM_EMAIL_DOMAIN}`);
}

/** Whether a primary address is the admin's: the pages that also show settings and every conversation. */
export function isTeamAdmin(primaryAddress: string | null | undefined): boolean {
  return primaryAddress?.trim().toLowerCase() === ADMIN_EMAIL;
}

type ClerkEmail = { emailAddress: string; verification?: { status?: string | null } | null };

/** A Clerk user's addresses as teamAccess() reads them. */
export function teamEmails(user: { emailAddresses: ClerkEmail[] } | null | undefined): TeamEmail[] | undefined {
  return user?.emailAddresses.map((email) => ({
    address: email.emailAddress,
    verified: email.verification?.status === "verified",
  }));
}
