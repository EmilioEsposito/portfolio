"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  Home, Settings, MessageSquare, FlaskConical,
  Calendar, Inbox, Search, ChevronLeft, Menu
} from "lucide-react"
import { useSidebar } from "@/components/ui/sidebar"
import { Button } from "@/components/ui/button"
import { Moon } from "lucide-react"
import { useEffect, useState } from "react"
import { useIsMobile } from "@/hooks/use-mobile"

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

export function AppSidebar() {
  const { toggleSidebar } = useSidebar()
  const [theme, setTheme] = useState<"light" | "dark">("light")
  const isMobile = useIsMobile()

  useEffect(() => {
    const root = window.document.documentElement
    root.classList.remove("light", "dark")
    root.classList.add(theme)
  }, [theme])

  const toggleSidebarIfMobile = () => {
    if (isMobile) {
      toggleSidebar()
    }
  }

  // Move items inside the component
  const items = [
    {
      title: "Home",
      url: "/",
      icon: Home,
      onClick: toggleSidebarIfMobile,
    },
    {
      title: 'Chat',
      url: '/chat',
      icon: MessageSquare,
      onClick: toggleSidebarIfMobile,
    },
    {
      title: 'Test',
      url: '/test',
      icon: FlaskConical,
      onClick: toggleSidebarIfMobile,
    },
    {
      title: "Inbox",
      url: "#",
      icon: Inbox,
      onClick: toggleSidebarIfMobile,
    },
    {
      title: "Calendar",
      url: "#",
      icon: Calendar,
      onClick: toggleSidebarIfMobile,
    },
    {
      title: "Search",
      url: "#",
      icon: Search,
      onClick: toggleSidebarIfMobile,
    },
    {
      title: "Settings",
      url: "#",
      icon: Settings,
      onClick: toggleSidebarIfMobile,
    },
    {
      title: "Light/Dark Mode",
      url: "#",
      icon: Moon,
      onClick: () => {
        setTheme(theme === "dark" ? "light" : "dark");
        console.log(`Changed theme to ${theme}`);
      },
    }
  ]

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="flex justify-end border-b border-sidebar-border">
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleSidebar}
          className="h-8 w-8"
        >
          <Menu className="h-4 w-4" />
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
                    <Link href={item.url} onClick={item.onClick}>
                      <item.icon />
                      <span>{item.title}</span>
                    </Link>
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
