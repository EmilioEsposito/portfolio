import { useState } from "react";
import { useAuth } from "@clerk/react-router";
import type { Route } from "./+types/examples.protected";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import { Input } from "~/components/ui/input";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Protected Endpoint Example | Emilio Esposito" },
    {
      name: "description",
      content:
        "Demonstrates accessing protected FastAPI endpoints with Clerk authentication",
    },
  ];
}

interface UserData {
  message: string;
  user_id: string;
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  session_id: string;
}

interface GoogleData {
  message?: string;
  first_result_content?: unknown;
  error?: string;
  scopes?: string[];
  token_info?: Record<string, unknown>;
  credential_info?: Record<string, unknown>;
}

export default function ProtectedExample() {
  const { getToken } = useAuth();
  const [userData, setUserData] = useState<UserData | null>(null);
  const [googleData, setGoogleData] = useState<GoogleData | null>(null);
  const [simpleData, setSimpleData] = useState<string | null>(null);
  const [serniacapitalData, setSerniacapitalData] = useState<string | null>(
    null
  );
  const [serniacapitalGetUserData, setSerniacapitalGetUserData] = useState<
    string | null
  >(null);
  const [error, setError] = useState<string | null>(null);
  const [googleError, setGoogleError] = useState<string | null>(null);
  const [simpleError, setSimpleError] = useState<string | null>(null);
  const [serniacapitalError, setSerniacapitalError] = useState<string | null>(
    null
  );
  const [serniacapitalGetUserError, setSerniacapitalGetUserError] = useState<
    string | null
  >(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isGoogleLoading, setIsGoogleLoading] = useState(false);
  const [isSimpleLoading, setIsSimpleLoading] = useState(false);
  const [isSerniacapitalLoading, setIsSerniacapitalLoading] = useState(false);
  const [isSerniacapitalGetUserLoading, setIsSerniacapitalGetUserLoading] =
    useState(false);
  const [adminPassword, setAdminPassword] = useState("");

  const fetchProtectedData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const token = await getToken();
      const response = await fetch("/api/examples/protected_get_user", {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(`${response.statusText}. ${data.detail}`);
      }

      setUserData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setIsLoading(false);
    }
  };

  const fetchProtectedGoogleData = async () => {
    setIsGoogleLoading(true);
    setGoogleError(null);
    try {
      const token = await getToken();
      const response = await fetch("/api/examples/protected_google", {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(`${response.statusText}. ${data.detail}`);
      }

      setGoogleData(data);
    } catch (err) {
      setGoogleError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setIsGoogleLoading(false);
    }
  };

  const fetchProtectedSimpleData = async () => {
    setIsSimpleLoading(true);
    setSimpleError(null);
    setSimpleData(null);
    try {
      const token = await getToken();
      const response = await fetch("/api/examples/protected_simple", {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      if (!response.ok) {
        let errorDetail = response.statusText;
        try {
          const errorData = await response.json();
          if (errorData && errorData.detail) {
            errorDetail = errorData.detail;
          }
        } catch {
          // If parsing JSON fails, use the status text
        }
        throw new Error(`${response.status}. ${errorDetail}`);
      }

      const data = await response.text();
      setSimpleData(data);
    } catch (err) {
      setSimpleError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setIsSimpleLoading(false);
    }
  };

  const fetchProtectedSerniacapitalData = async () => {
    setIsSerniacapitalLoading(true);
    setSerniacapitalError(null);
    setSerniacapitalData(null);
    try {
      const token = await getToken();
      const response = await fetch("/api/examples/protected_serniacapital", {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      if (!response.ok) {
        let errorDetail = response.statusText;
        try {
          const errorData = await response.json();
          if (errorData && errorData.detail) {
            errorDetail = errorData.detail;
          }
        } catch {
          /* If parsing JSON fails, use the status text */
        }
        throw new Error(`${response.status}. ${errorDetail}`);
      }

      const data = await response.text();
      setSerniacapitalData(data);
    } catch (err) {
      setSerniacapitalError(
        err instanceof Error ? err.message : "An error occurred"
      );
    } finally {
      setIsSerniacapitalLoading(false);
    }
  };

  const fetchProtectedSerniacapitalGetUserData = async () => {
    setIsSerniacapitalGetUserLoading(true);
    setSerniacapitalGetUserError(null);
    setSerniacapitalGetUserData(null);
    try {
      const token = await getToken();
      const response = await fetch(
        "/api/examples/protected_serniacapital_or_admin",
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ password: adminPassword }),
        }
      );

      if (!response.ok) {
        let errorDetail = response.statusText;
        try {
          const errorData = await response.json();
          if (errorData && errorData.detail) {
            errorDetail = errorData.detail;
          }
        } catch {
          /* If parsing JSON fails, use the status text */
        }
        throw new Error(`${response.status}. ${errorDetail}`);
      }

      const data = await response.text();
      setSerniacapitalGetUserData(data);
    } catch (err) {
      setSerniacapitalGetUserError(
        err instanceof Error ? err.message : "An error occurred"
      );
    } finally {
      setIsSerniacapitalGetUserLoading(false);
    }
  };

  return (
    <div className="container mx-auto py-10 space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Protected Endpoint Example</CardTitle>
          <CardDescription>
            This example demonstrates accessing a protected FastAPI endpoint
            using Clerk authentication
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button onClick={fetchProtectedData} disabled={isLoading}>
            {isLoading ? "Loading..." : "Fetch Protected Data"}
          </Button>

          {error && <div className="text-red-500 mt-4">Error: {error}</div>}

          {userData && (
            <div className="mt-4 space-y-2">
              <h3 className="font-medium">Response Data:</h3>
              <pre className="bg-muted p-4 rounded-lg overflow-auto">
                {JSON.stringify(userData, null, 2)}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Protected Simple Endpoint Example</CardTitle>
          <CardDescription>
            This example demonstrates accessing a protected FastAPI endpoint
            that only requires authentication.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button onClick={fetchProtectedSimpleData} disabled={isSimpleLoading}>
            {isSimpleLoading ? "Loading..." : "Fetch Protected Simple Data"}
          </Button>

          {simpleError && (
            <div className="text-red-500 mt-4">Error: {simpleError}</div>
          )}

          {simpleData && (
            <div className="mt-4 space-y-2">
              <h3 className="font-medium">Response Data:</h3>
              <pre className="bg-muted p-4 rounded-lg overflow-auto">
                {simpleData}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Protected Sernia Capital Route Example</CardTitle>
          <CardDescription>
            This example demonstrates accessing a protected FastAPI endpoint
            that requires Sernia Capital domain verification.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button
            onClick={fetchProtectedSerniacapitalData}
            disabled={isSerniacapitalLoading}
          >
            {isSerniacapitalLoading
              ? "Loading..."
              : "Fetch Sernia Capital Data"}
          </Button>

          {serniacapitalError && (
            <div className="text-red-500 mt-4">Error: {serniacapitalError}</div>
          )}

          {serniacapitalData && (
            <div className="mt-4 space-y-2">
              <h3 className="font-medium">Response Data:</h3>
              <pre className="bg-muted p-4 rounded-lg overflow-auto">
                {serniacapitalData}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Protected Sernia Capital Admin/User Route Example</CardTitle>
          <CardDescription>
            This example demonstrates accessing a protected FastAPI endpoint
            that requires Sernia Capital domain or admin verification.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Input
            type="password"
            placeholder="Optional: Admin Password"
            value={adminPassword}
            onChange={(e) => setAdminPassword(e.target.value)}
            className="mb-4"
          />
          <Button
            onClick={fetchProtectedSerniacapitalGetUserData}
            disabled={isSerniacapitalGetUserLoading}
          >
            {isSerniacapitalGetUserLoading
              ? "Loading..."
              : "Fetch Sernia Capital Admin/User Data"}
          </Button>

          {serniacapitalGetUserError && (
            <div className="text-red-500 mt-4">
              Error: {serniacapitalGetUserError}
            </div>
          )}

          {serniacapitalGetUserData && (
            <div className="mt-4 space-y-2">
              <h3 className="font-medium">Response Data:</h3>
              <pre className="bg-muted p-4 rounded-lg overflow-auto">
                {serniacapitalGetUserData}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Protected Google Endpoint Example</CardTitle>
          <CardDescription>
            This example demonstrates accessing a protected FastAPI endpoint
            that requires Google OAuth
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button
            onClick={fetchProtectedGoogleData}
            disabled={isGoogleLoading}
          >
            {isGoogleLoading ? "Loading..." : "Fetch Protected Google Data"}
          </Button>

          {googleError && (
            <div className="text-red-500 mt-4">Error: {googleError}</div>
          )}

          {googleData && (
            <div className="mt-4 space-y-2">
              <h3 className="font-medium">Response Data:</h3>
              <pre className="bg-muted p-4 rounded-lg overflow-auto">
                {JSON.stringify(googleData, null, 2)}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
