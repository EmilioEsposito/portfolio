import { useAuth, useUser, SignInButton } from "@clerk/react-router";
import { useLocation } from "react-router";
import { Button } from "~/components/ui/button";
import { Loader2, ShieldAlert } from "lucide-react";

interface SerniaAuthGuardProps {
  children: React.ReactNode;
  /** Optional custom message to display when not authenticated */
  message?: string;
  /** Optional custom title for the auth prompt */
  title?: string;
  /** Optional icon to display (defaults to ShieldAlert) */
  icon?: React.ReactNode;
}

/**
 * SerniaAuthGuard component that protects content requiring @serniacapital.com authentication.
 * Shows a loading state while auth is loading, a sign-in prompt when not authenticated,
 * and an access restricted message when signed in but not a serniacapital.com user.
 */
export function SerniaAuthGuard({
  children,
  message = "This page is restricted to @serniacapital.com users.",
  title = "Authentication Required",
  icon,
}: SerniaAuthGuardProps) {
  const { isLoaded, isSignedIn } = useAuth();
  const { user } = useUser();
  const location = useLocation();

  // Check if user has a verified @serniacapital.com email
  const isSerniaCapitalUser = user?.emailAddresses?.some(
    email => email.emailAddress.endsWith('@serniacapital.com') &&
             email.verification?.status === 'verified'
  ) ?? false;

  // Show loading state while auth is being determined
  if (!isLoaded) {
    return (
      <div className="flex items-center justify-center h-screen bg-background">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // Show sign-in prompt when not authenticated
  if (!isSignedIn) {
    return (
      <div className="flex flex-col items-center justify-center h-screen gap-6 px-4 bg-background">
        <div className="flex flex-col items-center gap-4 text-center">
          {icon || <ShieldAlert className="w-16 h-16 text-muted-foreground" />}
          <div className="space-y-2">
            <h2 className="text-2xl font-bold text-foreground">{title}</h2>
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

  // Signed in but not a serniacapital.com user
  if (!isSerniaCapitalUser) {
    return (
      <div className="flex flex-col items-center justify-center h-screen gap-6 px-4 bg-background">
        <div className="flex flex-col items-center gap-4 text-center">
          <ShieldAlert className="w-16 h-16 text-amber-500" />
          <div className="space-y-2">
            <h2 className="text-2xl font-bold text-foreground">Access Restricted</h2>
            <p className="text-muted-foreground max-w-md">
              This page is only available to users with a verified @serniacapital.com email address.
            </p>
            <p className="text-sm text-muted-foreground">
              Signed in as: <span className="font-medium">{user?.primaryEmailAddress?.emailAddress}</span>
            </p>
          </div>
        </div>
      </div>
    );
  }

  // User is authenticated and is a serniacapital.com user
  return <>{children}</>;
}
