import type { Route } from "./+types/scheduler";
import { Scheduler } from "~/components/scheduler";
import { SerniaAuthGuard } from "~/components/sernia-auth-guard";

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
  return (
    <SerniaAuthGuard>
      <div className="container mx-auto px-4 py-8 max-w-4xl">
        <h2 className="text-2xl font-bold mb-4">Scheduler Admin</h2>
        <Scheduler apiBaseUrl="/api" />
      </div>
    </SerniaAuthGuard>
  );
}
