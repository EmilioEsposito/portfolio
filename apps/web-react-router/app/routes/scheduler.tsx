import type { Route } from "./+types/scheduler";
import { Scheduler } from "~/components/scheduler";
import { useAuth } from "@clerk/react-router";
import { useState, useEffect } from "react";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Scheduler Admin | Emilio Esposito" },
    {
      name: "description",
      content: "Manage scheduled jobs and tasks",
    },
  ];
}

export default function SchedulerPage() {
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
      }
    };
    fetchToken();
  }, [getToken]);

  useEffect(() => {
    console.log("Token updated:", authToken);
  }, [authToken]);

  return (
    <div className="container mx-auto px-4 py-8 max-w-4xl">
      <h2 className="text-2xl font-bold mb-4">Scheduler Admin</h2>
      {tokenLoading ? (
        <p className="text-muted-foreground">Loading authentication...</p>
      ) : authToken ? (
        <Scheduler apiBaseUrl="/api" authToken={authToken} />
      ) : (
        <p className="text-muted-foreground">
          Could not authenticate. Scheduler disabled.
        </p>
      )}
    </div>
  );
}
