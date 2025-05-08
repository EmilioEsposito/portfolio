"use client";

import { Scheduler } from "@portfolio/features";
import { useAuth } from "@clerk/nextjs";
import React, { useState, useEffect } from 'react';

export default function Home() {
  const { getToken } = useAuth();
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [tokenLoading, setTokenLoading] = useState(true);

  useEffect(() => {
    const fetchToken = async () => {
      try {
        const token = await getToken();
        setAuthToken(token);
      } catch (error) {
        console.error("Error fetching auth token:", error);
        setAuthToken(null);
      } finally {
        setTokenLoading(false);
        console.log("Token loaded:", authToken);
      }
    };
    fetchToken();
  }, [getToken]);


  return (
    <div className="container mx-auto px-4 py-8 max-w-4xl">
      <h2>Scheduler Admin</h2>
      {tokenLoading ? (
        <p>Loading authentication...</p>
      ) : authToken ? (
        <Scheduler apiBaseUrl='/api' authToken={authToken} />
      ) : (
        <p>Could not authenticate. Scheduler disabled.</p>
      )}
    </div>
  );
}
