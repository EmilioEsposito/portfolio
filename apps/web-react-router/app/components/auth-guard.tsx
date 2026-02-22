import { useAuth, useUser, SignInButton } from "@clerk/react-router";
import { useLocation } from "react-router";
import { Button } from "~/components/ui/button";
import { Loader2, ShieldAlert } from "lucide-react";

interface AuthGuardProps {
  children: React.ReactNode;
  /** Optional custom message to display when not authenticated */
  message?: string;
  /** Optional custom title for the auth prompt */
  title?: string;
  /** Optional icon to display (defaults to ShieldAlert) */
  icon?: React.ReactNode;
  /** If set, requires a verified email from this domain (e.g. "serniacapital.com") */
  requireDomain?: string;
}

/**
 * AuthGuard component that protects content requiring authentication.
 * Shows a loading state while auth is loading, and a sign-in prompt when not authenticated.
 * When requireDomain is set, also verifies the user has a verified email from that domain.
 * After signing in, the user is redirected back to the protected page.
 */
export function AuthGuard({
  children,
  message = "Please sign in to access this page",
  title = "Authentication Required",
  icon,
  requireDomain,
}: AuthGuardProps) {
  const { isLoaded, isSignedIn } = useAuth();
  const { user } = useUser();
  const location = useLocation();

  // Show loading state while auth is being determined
  if (!isLoaded) {
    return (
      <div className="flex items-center justify-center h-[calc(100dvh-52px)]">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // Show sign-in prompt when not authenticated
  if (!isSignedIn) {
    return (
      <div className="flex flex-col items-center justify-center h-[calc(100dvh-52px)] gap-6 px-4">
        <div className="flex flex-col items-center gap-4 text-center">
          {icon || <ShieldAlert className="w-16 h-16 text-muted-foreground" />}
          <div className="space-y-2">
            <h2 className="text-2xl font-bold">{title}</h2>
            <p className="text-muted-foreground max-w-md">{message}</p>
          </div>
        </div>
        <SignInButton
          mode="modal"
          forceRedirectUrl={location.pathname + location.search}
        >
          <Button size="lg" className="gap-2">
            Sign In to Continue
          </Button>
        </SignInButton>
      </div>
    );
  }

  // Check domain requirement if specified
  if (requireDomain) {
    const hasDomainEmail = user?.emailAddresses?.some(
      (email) =>
        email.emailAddress.endsWith(`@${requireDomain}`) &&
        email.verification?.status === "verified",
    );

    if (!hasDomainEmail) {
      return (
        <div className="flex flex-col items-center justify-center h-[calc(100dvh-52px)] gap-6 px-4">
          <div className="flex flex-col items-center gap-4 text-center">
            {icon || <ShieldAlert className="w-16 h-16 text-muted-foreground" />}
            <div className="space-y-2">
              <h2 className="text-2xl font-bold">Access Restricted</h2>
              <p className="text-muted-foreground max-w-md">
                This page requires a verified <strong>@{requireDomain}</strong> email address.
              </p>
            </div>
          </div>
        </div>
      );
    }
  }

  // User is authenticated (and authorized if domain required), render protected content
  return <>{children}</>;
}
