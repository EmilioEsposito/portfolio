import type { Route } from "./+types/scheduler";
import { Scheduler } from "~/sites/team/components/scheduler";
import { teamTitle } from "~/sites/team/lib/team-meta";

export function meta({}: Route.MetaArgs) {
  return [
    { title: teamTitle("Scheduler") },
    {
      name: "description",
      content: "Manage scheduled jobs and tasks",
    },
  ];
}

export default function SchedulerPage() {
  return (
    <div className="container mx-auto px-4 py-8 max-w-4xl">
      <h2 className="text-2xl font-bold mb-4">Scheduler Admin</h2>
      <Scheduler apiBaseUrl="/api" />
    </div>
  );
}
