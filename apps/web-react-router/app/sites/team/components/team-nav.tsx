import { UserButton, useUser } from "@clerk/react-router";
import { Globe, Menu } from "lucide-react";
import { Link, useLocation, useRouteLoaderData } from "react-router";

import { Button } from "~/components/ui/button";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "~/components/ui/sidebar";
import { TeamWordmark } from "~/sites/team/components/team-wordmark";
import { isTeamAdmin } from "~/sites/team/lib/team-access";
import { isCurrentSection, TEAM_HOME, visibleSections } from "~/sites/team/lib/team-sections";

// The team's navigation, built from the sidebar primitives in
// ~/components/ui/sidebar. TeamSidebar is the whole sidebar of most team
// pages; the chat, its settings and the conversations table keep their
// conversation sidebar, which holds TeamNavItems and TeamAccount too.

/** The team layout's route id, whose loader says where the public site is. */
export const TEAM_LAYOUT_ROUTE = "sites/team/routes/layout";

export type TeamLayoutData = {
  /** The Sernia public site from this host, or null where it is not served. */
  publicSiteHref: string | null;
};

/** Whether the signed-in user is the admin, who also sees settings and every conversation. */
export function useTeamAdmin(): boolean {
  const { user } = useUser();
  return isTeamAdmin(user?.primaryEmailAddress?.emailAddress);
}

/** Closes the sidebar on a phone once a link in it is followed. */
function useCloseOnPhone() {
  const { isMobile, setOpenMobile } = useSidebar();
  return () => {
    if (isMobile) setOpenMobile(false);
  };
}

/** The team home and every section the user may open, the current one marked. */
export function TeamNavItems() {
  const { pathname } = useLocation();
  const admin = useTeamAdmin();
  const close = useCloseOnPhone();
  return (
    <SidebarMenu>
      {[TEAM_HOME, ...visibleSections(admin)].map((section) => {
        const current = isCurrentSection(section, pathname);
        return (
          <SidebarMenuItem key={section.url}>
            <SidebarMenuButton asChild isActive={current} tooltip={section.title}>
              <Link to={section.url} onClick={close} aria-current={current ? "page" : undefined}>
                <section.icon />
                <span>{section.title}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        );
      })}
    </SidebarMenu>
  );
}

/** The account button, and a link to the public site. */
export function TeamAccount() {
  const data = useRouteLoaderData(TEAM_LAYOUT_ROUTE) as TeamLayoutData | undefined;
  const { user } = useUser();
  const { state, isMobile } = useSidebar();
  const collapsed = state === "collapsed" && !isMobile;
  return (
    <SidebarMenu>
      {data?.publicSiteHref && (
        <SidebarMenuItem>
          <SidebarMenuButton asChild tooltip="Public site">
            {/* A full page load: the public site runs without Clerk. */}
            <a href={data.publicSiteHref}>
              <Globe />
              <span>Public site</span>
            </a>
          </SidebarMenuButton>
        </SidebarMenuItem>
      )}
      <SidebarMenuItem>
        <div className="flex h-10 items-center gap-2 px-1.5">
          {/* Clerk draws the button once its script has loaded; the box keeps the row from moving. */}
          <span className="flex size-7 shrink-0 items-center justify-center">
            <UserButton />
          </span>
          {!collapsed && <span className="truncate text-sm text-muted-foreground">{user?.primaryEmailAddress?.emailAddress}</span>}
        </div>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}

/** The sidebar of the team pages that do not bring their own. */
export function TeamSidebar() {
  const { state, toggleSidebar, isMobile } = useSidebar();
  const collapsed = state === "collapsed" && !isMobile;
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="border-b border-sidebar-border">
        <div className="flex h-8 items-center justify-between gap-1 px-1">
          {!collapsed && <TeamWordmark linked className="text-sm [&_svg]:size-6" />}
          <Button variant="ghost" size="icon" onClick={toggleSidebar} className="h-8 w-8 shrink-0">
            <Menu className="h-4 w-4" />
            <span className="sr-only">Toggle sidebar</span>
          </Button>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Team</SidebarGroupLabel>
          <SidebarGroupContent>
            <TeamNavItems />
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter className="border-t border-sidebar-border">
        <TeamAccount />
      </SidebarFooter>
    </Sidebar>
  );
}

/** The bar above a team page: on a phone, the menu button and the wordmark. */
export function TeamTopBar() {
  const { toggleSidebar } = useSidebar();
  return (
    <div className="flex h-[52px] shrink-0 items-center gap-2 border-b px-2 md:hidden">
      <Button variant="ghost" onClick={toggleSidebar} className="h-10 w-10 p-2">
        <Menu className="h-6 w-6" />
        <span className="sr-only">Open the team menu</span>
      </Button>
      <TeamWordmark linked className="text-sm [&_svg]:size-6" />
    </div>
  );
}
