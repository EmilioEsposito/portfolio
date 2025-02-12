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
} from "lucide-react";
import { useSidebar } from "@/components/ui/sidebar";
import { Button } from "@/components/ui/button";
import { useEffect, useState } from "react";
import { useIsMobile } from "@/hooks/use-mobile";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";

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
  const { toggleSidebar } = useSidebar();
  const { theme, setTheme } = useTheme();
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
      ],
    },
    {
      label: "AI & LLM",
      items: [
        {
          type: "navigation",
          title: "Chat",
          url: "/chat",
          icon: MessageSquare,
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
      ],
    },
    {
      label: "Technical Docs",
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
      ],
    },
    {
      label: "Settings",
      items: [
        {
          type: "action",
          title: "Toggle theme",
          icon: !mounted ? Moon : theme === "dark" ? Sun : Moon,
          onClick: () => {
            setTheme(theme === "dark" ? "light" : "dark");
          },
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
          // size="sm"
          onClick={toggleSidebar}
          className="h-10 w-10 p-2"
        >
          <Menu className="h-6 w-6" />
          <span className="sr-only">Toggle sidebar</span>
        </Button>
      </SidebarHeader>
      <SidebarContent>
        {sidebarSections.map((section) => (
          <SidebarGroup key={section.label}>
            <SidebarGroupLabel>{section.label}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {section.items.map((item) => (
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
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
    </Sidebar>
  );
}
