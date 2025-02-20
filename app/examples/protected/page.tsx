'use client';

import { useAuth } from "@clerk/nextjs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useState } from "react";

interface UserData {
  message: string;
  user_id: string;
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  session_id: string;
}

interface GoogleData {
  scopes: string[];
  token_info: Record<string, any>;
}

export default function ProtectedExample() {
  const { getToken } = useAuth();
  const [userData, setUserData] = useState<UserData | null>(null);
  const [googleData, setGoogleData] = useState<GoogleData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [googleError, setGoogleError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isGoogleLoading, setIsGoogleLoading] = useState(false);

  const fetchProtectedData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const token = await getToken();
      const response = await fetch('/api/examples/protected_get_user', {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });

      const data = await response.json();
      
      if (!response.ok) {
        throw new Error(`${response.statusText}. ${data.detail}`);
      }
      
      setUserData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setIsLoading(false);
    }
  };

  const fetchProtectedGoogleData = async () => {
    setIsGoogleLoading(true);
    setGoogleError(null);
    try {
      const token = await getToken();
      const response = await fetch('/api/examples/protected_google', {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });

      const data = await response.json();
      
      if (!response.ok) {
        throw new Error(`${response.statusText}. ${data.detail}`);
      }
      
      setGoogleData(data);
    } catch (err) {
      setGoogleError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setIsGoogleLoading(false);
    }
  };

  return (
    <div className="container mx-auto py-10 space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Protected Endpoint Example</CardTitle>
          <CardDescription>
            This example demonstrates accessing a protected FastAPI endpoint using Clerk authentication
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button 
            onClick={fetchProtectedData}
            disabled={isLoading}
          >
            {isLoading ? 'Loading...' : 'Fetch Protected Data'}
          </Button>

          {error && (
            <div className="text-red-500 mt-4">
              Error: {error}
            </div>
          )}

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
          <CardTitle>Protected Google Endpoint Example</CardTitle>
          <CardDescription>
            This example demonstrates accessing a protected FastAPI endpoint that requires Google OAuth
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button 
            onClick={fetchProtectedGoogleData}
            disabled={isGoogleLoading}
          >
            {isGoogleLoading ? 'Loading...' : 'Fetch Protected Google Data'}
          </Button>

          {googleError && (
            <div className="text-red-500 mt-4">
              Error: {googleError}
            </div>
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