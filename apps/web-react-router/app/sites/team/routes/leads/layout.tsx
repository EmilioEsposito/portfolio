import { NavLink, Outlet, useLocation } from "react-router";

import { cn } from "~/lib/utils";
import { LEADS_PATH, isLeadsPath } from "~/sites/team/lib/lead-inbox";
import { LEAD_TABS } from "~/sites/team/lib/team-sections";

// The lead agent's pages (/team/leads/*), as tabs: the leads list (each lead
// has its page under it), its listing data, its settings, showing bookings,
// and how it works.

export default function LeadAgentLayout() {
  const { pathname } = useLocation();
  return (
    // The team layout's SidebarInset is the page's <main>.
    <div className="mx-auto w-full max-w-7xl px-5 py-6 sm:px-8 sm:py-12">
      <header className="mb-4 sm:mb-6">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-4xl">Lead agent</h1>
      </header>
      {/* One scrolling row on a phone, so the page starts higher. */}
      <nav
        aria-label="Lead agent pages"
        className="-mx-5 mb-5 flex gap-x-5 overflow-x-auto border-b px-5 sm:mx-0 sm:mb-7 sm:flex-wrap sm:gap-y-1 sm:overflow-visible sm:px-0"
      >
        {LEAD_TABS.map((tab) => (
          <NavLink
            key={tab.url}
            to={tab.url}
            end
            className={({ isActive: exact, isPending }) => {
              // A lead's page is under the Leads tab.
              const isActive = exact || (tab.url === LEADS_PATH && isLeadsPath(pathname));
              return cn(
                "flex shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-1 pb-3 pt-1 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                isActive ? "border-foreground text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
                isPending && "animate-pulse text-foreground",
              );
            }}
          >
            {tab.title}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  );
}
