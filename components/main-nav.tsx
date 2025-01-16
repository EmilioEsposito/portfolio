"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { Home, Settings, MessageSquare } from "lucide-react"
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
} from "@/components/ui/sidebar"

export function MainNav() {
  const pathname = usePathname()

  const routes = [
    {
      label: 'Home',
      icon: Home,
      href: '/',
    },
    {
      label: 'Chat',
      icon: MessageSquare,
      href: '/chat',
    },
    {
      label: 'Settings',
      icon: Settings,
      href: '/settings',
    }
  ]

  return (
    <Sidebar>
      <SidebarHeader>
        <h2 className="text-lg font-semibold">My App</h2>
      </SidebarHeader>
      <SidebarContent>
        <SidebarMenu>
          {routes.map((route) => (
            <SidebarMenuItem key={route.href}>
              <Link href={route.href}>
                <SidebarMenuButton 
                  isActive={pathname === route.href}
                  tooltip={route.label}
                >
                  <route.icon />
                  <span>{route.label}</span>
                </SidebarMenuButton>
              </Link>
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarContent>
    </Sidebar>
  )
} 