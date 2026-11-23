import { NavLink, Outlet } from "react-router";

import { cn } from "~/lib/utils";
import { LEAD_TABS } from "~/sites/team/lib/team-sections";

// The lead agent's pages (/team/leads/*): an overview of how it works, then
// its listing data, its settings and showing bookings, as tabs.

export default function LeadAgentLayout() {
  return (
    // The team layout's SidebarInset is the page's <main>.
    <div className="mx-auto w-full max-w-7xl px-5 py-9 sm:px-8 sm:py-12">
      <header className="mb-6">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Lead agent</h1>
      </header>
      <nav aria-label="Lead agent pages" className="mb-7 flex flex-wrap gap-x-5 gap-y-1 border-b">
        {LEAD_TABS.map((tab) => (
          <NavLink
            key={tab.url}
            to={tab.url}
            end
            className={({ isActive, isPending }) =>
              cn(
                "flex shrink-0 items-center gap-2 border-b-2 px-1 pb-3 pt-1 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                isActive ? "border-foreground text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
                isPending && "animate-pulse text-foreground",
              )
            }
          >
            {tab.title}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  );
}
