"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  Home, Settings, MessageSquare, FlaskConical,
  Calendar, Inbox, Search, ChevronLeft, Menu, Moon, LucideIcon, Building, Sun
} from "lucide-react"
import { useSidebar } from "@/components/ui/sidebar"
import { Button } from "@/components/ui/button"
import { useEffect, useState } from "react"
import { useIsMobile } from "@/hooks/use-mobile"
import { useTheme } from "next-themes"
import { cn } from "@/lib/utils"

import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarHeader
} from "@/components/ui/sidebar"

type SidebarItem = {
  title: string;
  icon: LucideIcon;
  onClick: (e: React.MouseEvent) => void;
} & (
  | { type: 'navigation'; url: string }
  | { type: 'action' }
)

export function AppSidebar() {
  const { toggleSidebar } = useSidebar()
  const { theme, setTheme } = useTheme()
  const isMobile = useIsMobile()
  const [mounted, setMounted] = useState(false)

  // useEffect only runs on the client, so now we can safely show the UI
  useEffect(() => {
    setMounted(true)
  }, [])

  const toggleSidebarIfMobile = () => {
    if (isMobile) {
      toggleSidebar()
    }
  }

  const items: SidebarItem[] = [
    {
      type: 'navigation',
      title: "Home",
      url: "/",
      icon: Home,
      onClick: toggleSidebarIfMobile,
    },
    {
      type: 'navigation',
      title: 'Chat',
      url: '/chat',
      icon: MessageSquare,
      onClick: toggleSidebarIfMobile,
    },
    // {
    //   type: 'navigation',
    //   title: 'Test',
    //   url: '/test',
    //   icon: FlaskConical,
    //   onClick: toggleSidebarIfMobile,
    // },
    {
      type: 'navigation',
      title: "Tenant Mass Messaging",
      url: "/tenant-mass-messaging",
      icon: Building,
      onClick: toggleSidebarIfMobile,
    },
    // {
    //   type: 'navigation',
    //   title: "Inbox",
    //   url: "#",
    //   icon: Inbox,
    //   onClick: toggleSidebarIfMobile,
    // },
    // {
    //   type: 'navigation',
    //   title: "Calendar",
    //   url: "#",
    //   icon: Calendar,
    //   onClick: toggleSidebarIfMobile,
    // },
    // {
    //   type: 'navigation',
    //   title: "Search",
    //   url: "#",
    //   icon: Search,
    //   onClick: toggleSidebarIfMobile,
    // },
    // {
    //   type: 'navigation',
    //   title: "Settings",
    //   url: "#",
    //   icon: Settings,
    //   onClick: toggleSidebarIfMobile,
    // },
    {
      type: 'navigation',
      title: "API Docs",
      url: "/api/docs",
      icon: FlaskConical,
      onClick: toggleSidebarIfMobile,
    },
    {
      type: 'action',
      title: "Toggle theme",
      icon: !mounted ? Moon : theme === 'dark' ? Sun : Moon,
      onClick: () => {
        setTheme(theme === "dark" ? "light" : "dark")
      },
    },
  ]

  // Prevent hydration mismatch by not rendering the sidebar until mounted
  if (!mounted) {
    return null
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
        <SidebarGroup>
          <SidebarGroupLabel>Application</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {items.map((item) => (
                <SidebarMenuItem key={item.title}>
                  <SidebarMenuButton asChild tooltip={item.title}>
                    {item.type === 'navigation' ? (
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
      </SidebarContent>
    </Sidebar>
  )
}
