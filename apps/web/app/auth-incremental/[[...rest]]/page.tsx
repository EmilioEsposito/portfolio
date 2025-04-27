"use client";

import { UserProfile, useUser } from "@clerk/nextjs";

// Debug component to show OAuth scopes
function OAuthDebug() {
  const { user } = useUser();
  
  if (!user) return null;

  const googleAccount = user.externalAccounts.find(
    account => account.provider === "google"
  );

  return (
    <div className="p-4 m-4 bg-secondary rounded-lg">
      <h2 className="font-bold mb-2">OAuth Debug Info:</h2>
      <pre className="whitespace-pre-wrap">
        {JSON.stringify(
          {
            provider: googleAccount?.provider,
            accountDetails: googleAccount,
          },
          null,
          2
        )}
      </pre>
    </div>
  );
}

function ProfilePage() {
  return (
    <>
      <OAuthDebug />
      <UserProfile
        path="/auth-incremental"
        routing="path"
        additionalOAuthScopes={{
          google: [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send"
          ],
        }}
      />
    </>
  );
}
export default ProfilePage; 