import type { Route } from "./+types/scheduler";
import { Scheduler } from "~/components/scheduler";
import { useAuth } from "@clerk/react-router";

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
  const { isLoaded, isSignedIn } = useAuth();

  return (
    <div className="container mx-auto px-4 py-8 max-w-4xl">
      <h2 className="text-2xl font-bold mb-4">Scheduler Admin</h2>
      {!isLoaded ? (
        <p className="text-muted-foreground">Loading authentication...</p>
      ) : isSignedIn ? (
        <Scheduler apiBaseUrl="/api" />
      ) : (
        <p className="text-muted-foreground">
          Could not authenticate. Scheduler disabled.
        </p>
      )}
    </div>
  );
}
