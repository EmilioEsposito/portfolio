import {
  Bot,
  CalendarClock,
  Home,
  Inbox,
  LayoutList,
  ListChecks,
  MessagesSquare,
  Settings,
  Smartphone,
  type LucideIcon,
} from "lucide-react";

// The team's pages as the navigation and the team home list them. The paths
// are the backend's too (api/src/utils/team_links.py), so they do not change
// without it.

export type TeamSection = {
  title: string;
  /** One line on what the page is for, on the team home. */
  description: string;
  url: string;
  icon: LucideIcon;
  /** Shown only to the admin, as before the move. */
  adminOnly?: boolean;
};

export const TEAM_HOME: TeamSection = {
  title: "Team home",
  description: "Approvals, open tasks and every team page.",
  url: "/team",
  icon: Home,
};

export const TEAM_SECTIONS: TeamSection[] = [
  {
    title: "Sernia AI",
    description: "Ask the general agent, and approve or reject what it asks to do.",
    url: "/team/chat",
    icon: Bot,
  },
  {
    title: "Lead agent",
    description: "Zillow leads: listing data, the agent's settings and showing bookings.",
    url: "/team/leads",
    icon: Inbox,
  },
  {
    title: "Tasks",
    description: "Open ClickUp maintenance tasks and Quo tasks: see them, and mark them complete.",
    url: "/team/tasks",
    icon: ListChecks,
  },
  {
    title: "Message tenants",
    description: "Text a group of tenants, chosen by building, unit or lease.",
    url: "/team/tenants",
    icon: MessagesSquare,
  },
  {
    title: "Scheduler",
    description: "See, run and remove scheduled jobs.",
    url: "/team/scheduler",
    icon: CalendarClock,
  },
  {
    title: "General agent settings",
    description: "Sernia AI's triggers, schedule and model.",
    url: "/team/settings",
    icon: Settings,
    adminOnly: true,
  },
  {
    title: "Push devices",
    description: "See which team members have registered for notifications.",
    url: "/team/push-devices",
    icon: Smartphone,
    adminOnly: true,
  },
  {
    title: "All conversations",
    description: "Every Sernia AI conversation in one table.",
    url: "/team/admin",
    icon: LayoutList,
    adminOnly: true,
  },
];

/** The lead agent's pages, shown as tabs under /team/leads. */
export const LEAD_TABS = [
  { url: "/team/leads", title: "Overview" },
  { url: "/team/leads/listings", title: "Rental listing data" },
  { url: "/team/leads/settings", title: "Agent settings" },
  { url: "/team/leads/bookings", title: "Booking settings" },
] as const;

/** The sections a user sees: every one, less the admin's unless they are the admin. */
export function visibleSections(admin: boolean): TeamSection[] {
  return TEAM_SECTIONS.filter((section) => admin || !section.adminOnly);
}

/** Whether a section is the current page or the page holds it: /team/leads for /team/leads/bookings. */
export function isCurrentSection(section: TeamSection, pathname: string): boolean {
  const path = pathname.replace(/\/+$/, "").toLowerCase() || "/";
  if (section.url === TEAM_HOME.url) return path === section.url;
  return path === section.url || path.startsWith(`${section.url}/`);
}
