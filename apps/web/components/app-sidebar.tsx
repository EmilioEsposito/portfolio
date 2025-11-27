"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Home,
  Settings,
  MessageSquare,
  FlaskConical,
  Calendar,
  Inbox,
  Search,
  ChevronLeft,
  Menu,
  Moon,
  LucideIcon,
  Building,
  Sun,
  Blocks,
  MessagesSquare,
} from "lucide-react";
import { useSidebar } from "@/components/ui/sidebar";
import { Button } from "@/components/ui/button";
import { useEffect, useState } from "react";
import { useIsMobile } from "@/hooks/use-mobile";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { AccountSwitcher } from "@/components/google/account-switcher"
import { useUser } from "@clerk/nextjs";

import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarHeader,
} from "@/components/ui/sidebar";

type BaseSidebarItem = {
  title: string;
  icon: LucideIcon;
  onClick: (e: React.MouseEvent) => void;
};

type NavigationItem = BaseSidebarItem & {
  type: "navigation";
  url: string;
};

type ActionItem = BaseSidebarItem & {
  type: "action";
};

type SidebarItem = NavigationItem | ActionItem;

type SidebarSection = {
  label: string;
  items: SidebarItem[];
};

export function AppSidebar() {
  const { toggleSidebar, state } = useSidebar();
  const { theme, setTheme } = useTheme();
  const { user } = useUser();
  const isMobile = useIsMobile();
  const [mounted, setMounted] = useState(false);

  // useEffect only runs on the client, so now we can safely show the UI
  useEffect(() => {
    setMounted(true);
  }, []);

  const toggleSidebarIfMobile = () => {
    if (isMobile) {
      toggleSidebar();
    }
  };

  // Determine if user is a verified SerniaCapital user
  const isSerniaCapitalUser = user?.emailAddresses?.some(
    email => email.emailAddress.endsWith('@serniacapital.com') && 
             email.verification?.status === 'verified'
  ) ?? false;

  const sidebarSections: SidebarSection[] = [
    {
      label: "About",
      items: [
        {
          type: "navigation",
          title: "Home",
          url: "/",
          icon: Home,
          onClick: toggleSidebarIfMobile,
        },
        {
          type: "navigation",
          title: "Schedule Meeting",
          url: "/calendly",
          icon: Calendar,
          onClick: toggleSidebarIfMobile,
        },
      ],
    },
    {
      label: "AI Agents",
      items: [
        {
          type: "navigation",
          title: "Multi-Agent AI Assistant",
          url: "/multi-agent-chat",
          icon: MessageSquare,
          onClick: toggleSidebarIfMobile,
        },
        {
          type: "navigation",
          title: "Chat about Emilio",
          url: "/chat-emilio",
          icon: MessageSquare,
          onClick: toggleSidebarIfMobile,
        },
        {
          type: "navigation",
          title: "AI Weather Agent",
          url: "/chat-weather",
          icon: MessageSquare,
          onClick: toggleSidebarIfMobile,
        },
        {
          type: "navigation",
          title: "AI Email Responder",
          url: "/ai-email-responder",
          icon: Inbox,
          onClick: toggleSidebarIfMobile,
        },
      ],
    },
    {
      label: "Sernia Capital Production Apps",
      items: [
        {
          type: "navigation",
          title: "Tenant Mass Messaging",
          url: "/tenant-mass-messaging",
          icon: Building,
          onClick: toggleSidebarIfMobile,
        },
        ...(isSerniaCapitalUser ? [{
          type: "navigation" as const,
          title: "Message Tenants",
          url: "/message-tenants",
          icon: MessagesSquare,
          onClick: toggleSidebarIfMobile,
        }] : []),
      ],
    },
    {
      label: "Pure Technical",
      items: [
        {
          type: "navigation",
          title: "API Docs",
          url: "/api/docs",
          icon: FlaskConical,
          onClick: toggleSidebarIfMobile,
        },
        {
          type: "navigation",
          title: "Examples",
          url: "/examples",
          icon: Blocks,
          onClick: toggleSidebarIfMobile,
        },
        ...(isSerniaCapitalUser ? [{
          type: "navigation" as const,
          title: "Scheduler Admin",
          url: "/scheduler",
          icon: Calendar,
          onClick: toggleSidebarIfMobile,
        }] : []),
      ],
    },
    {
      label: "Settings",
      items: [
        {
          type: "action",
          title: "toggle-theme",
          icon: !mounted ? Moon : theme === "dark" ? Moon : Sun,
          onClick: () => {}, // We'll handle the click in the custom render below
        },
      ],
    },
  ];

  // Prevent hydration mismatch by not rendering the sidebar until mounted
  if (!mounted) {
    return null;
  }

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="flex justify-end border-b border-sidebar-border">
        <Button
          variant="ghost"
          onClick={toggleSidebar}
          className="h-8 w-8 p-2"
        >
          <Menu className="h-4 w-4" />
          <span className="sr-only">Toggle sidebar</span>
        </Button>
      </SidebarHeader>
      <SidebarContent>
        {sidebarSections.map((section) => (
          <SidebarGroup key={section.label}>
            <SidebarGroupLabel>{section.label}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {section.items.map((item) => 
                  item.title === "toggle-theme" ? (
                    // Only render theme toggle when expanded
                    state === "expanded" && (
                      <SidebarMenuItem key={item.title}>
                        <div className="flex w-full items-center justify-between px-3 py-2">
                          <div className="flex items-center gap-2">
                            <item.icon className="h-4 w-4" />
                            <Label htmlFor="dark-mode">
                              Light/Dark mode
                            </Label>
                          </div>
                          <Switch
                            id="dark-mode"
                            checked={theme === "dark"}
                            onCheckedChange={(checked) =>
                              setTheme(checked ? "dark" : "light")
                            }
                          />
                        </div>
                      </SidebarMenuItem>
                    )
                  ) : (
                    // All other items
                    <SidebarMenuItem key={item.title}>
                      <SidebarMenuButton asChild tooltip={item.title}>
                        {item.type === "navigation" ? (
                          <Link href={item.url} onClick={item.onClick}>
                            <item.icon />
                            <span>{item.title}</span>
                          </Link>
                        ) : (
                          <button onClick={item.onClick} className="flex w-full">
                            <item.icon />
                            <span>{item.title}</span>
                          </button>
                        )}
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  )
                )}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}

        {/* Add Account Switcher at the bottom */}
        {/* <SidebarGroup className="relative flex w-full min-w-0 flex-col p-2">
          <SidebarGroupLabel className="duration-200 flex h-8 shrink-0 items-center rounded-md px-2 text-xs font-medium text-sidebar-foreground/70 outline-none ring-sidebar-ring transition-[margin,opa] ease-linear focus-visible:ring-2 [&>svg]:size-4 [&>svg]:shrink-0 group-data-[collapsible=icon]:-mt-8 group-data-[collapsible=icon]:opacity-0">
            Account
          </SidebarGroupLabel>
          <SidebarGroupContent className="w-full text-sm">
            <SidebarMenu className="flex w-full min-w-0 flex-col gap-1">
              <SidebarMenuItem className="group/menu-item relative">
                <AccountSwitcher 
                  isCollapsed={state === "collapsed"}
                />
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup> */}
      </SidebarContent>
    </Sidebar>
  );
}
