import type { Route } from "./+types/scheduler";
import { Scheduler } from "~/sites/team/components/scheduler";
import { teamTitle } from "~/sites/team/lib/team-meta";

export function meta({}: Route.MetaArgs) {
  return [
    { title: teamTitle("Scheduler") },
    {
      name: "description",
      content: "What each scheduled job does and when it runs next; run or delete one.",
    },
  ];
}

export default function SchedulerPage() {
  return (
    // The team layout's SidebarInset is the page's <main>.
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 sm:py-10">
      <Scheduler apiBaseUrl="/api" />
    </div>
  );
}
