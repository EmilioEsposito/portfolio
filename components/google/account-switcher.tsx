'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useToast } from '@/components/ui/use-toast'
import { Button } from '@/components/ui/button'
import { LogOut, LogIn } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

interface UserInfo {
  email: string
  picture: string
  name: string
}

interface AccountSwitcherProps {
  className?: string
  isCollapsed?: boolean
}

export function AccountSwitcher({ className, isCollapsed = false }: AccountSwitcherProps) {
  const router = useRouter()
  const { toast } = useToast()
  const [isLoading, setIsLoading] = useState(false)
  const [userInfo, setUserInfo] = useState<UserInfo | null>(null)

  // Fetch user info on mount
  useEffect(() => {
    const checkAuth = async () => {
      try {
        const response = await fetch('/api/google/auth/check')
        const data = await response.json()
        
        if (data.authenticated) {
          setUserInfo({
            email: data.user_id,
            picture: data.picture || 'https://www.google.com/favicon.ico',
            name: data.name || data.user_id
          })
        } else {
          setUserInfo(null)
        }
      } catch (error) {
        console.error('Auth check failed:', error)
      }
    }
    
    checkAuth()
  }, [])

  const handleLogin = async () => {
    setIsLoading(true)
    try {
      const state = Math.random().toString(36).substring(2)
      sessionStorage.setItem('oauth_state', state)
      
      const authUrlResponse = await fetch('/api/google/auth/url', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ state })
      })
      const data = await authUrlResponse.json()
      
      // Add prompt=select_account to force account selection
      const authUrl = new URL(data.url)
      authUrl.searchParams.set('prompt', 'select_account')
      
      // Redirect to Google OAuth
      window.location.href = authUrl.toString()
    } catch (error) {
      console.error('Failed to initiate auth:', error)
      toast({
        title: "Error",
        description: "Failed to start authentication process",
        variant: "destructive"
      })
    } finally {
      setIsLoading(false)
    }
  }

  const handleLogout = async () => {
    setIsLoading(true)
    try {
      await fetch('/api/google/auth/logout', {
        method: 'POST'
      })
      setUserInfo(null)
      toast({
        title: "Logged out",
        description: "Successfully logged out of Google account"
      })
    } catch (error) {
      console.error('Failed to logout:', error)
      toast({
        title: "Error",
        description: "Failed to logout",
        variant: "destructive"
      })
    } finally {
      setIsLoading(false)
    }
  }

  const handleSwitchAccount = async () => {
    setIsLoading(true)
    try {
      // First, log out the current user
      await fetch('/api/google/auth/logout', {
        method: 'POST'
      })

      // Then initiate a new OAuth flow with force prompt
      const state = Math.random().toString(36).substring(2)
      sessionStorage.setItem('oauth_state', state)
      
      const authUrlResponse = await fetch('/api/google/auth/url', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ state })
      })
      const data = await authUrlResponse.json()
      
      // Add prompt=select_account to force account selection
      const authUrl = new URL(data.url)
      authUrl.searchParams.set('prompt', 'select_account')
      
      // Redirect to Google OAuth
      window.location.href = authUrl.toString()
    } catch (error) {
      console.error('Failed to switch account:', error)
      toast({
        title: "Error",
        description: "Failed to switch Google account",
        variant: "destructive"
      })
    } finally {
      setIsLoading(false)
    }
  }

  // Collapsed state - just show icon
  if (isCollapsed) {
    return (
      <button
        onClick={userInfo ? handleSwitchAccount : handleLogin}
        disabled={isLoading}
        className="flex w-full items-center gap-2 overflow-hidden rounded-md p-2 text-left outline-none ring-sidebar-ring transition-[width,height,padding] focus-visible:ring-2 active:bg-sidebar-accent active:text-sidebar-accent-foreground disabled:pointer-events-none disabled:opacity-50 group-has-[[data-sidebar=menu-action]]/menu-item:pr-8 aria-disabled:pointer-events-none aria-disabled:opacity-50 data-[active=true]:bg-sidebar-accent data-[active=true]:font-medium data-[active=true]:text-sidebar-accent-foreground data-[state=open]:hover:bg-sidebar-accent data-[state=open]:hover:text-sidebar-accent-foreground group-data-[collapsible=icon]:!size-8 group-data-[collapsible=icon]:!p-2 [&>span:last-child]:truncate [&>svg]:size-4 [&>svg]:shrink-0 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground h-8 text-sm"
      >
        <img 
          src={userInfo?.picture || 'https://www.google.com/favicon.ico'}
          alt={userInfo?.name || 'Google'} 
          className="size-4 rounded-full"
        />
      </button>
    )
  }

  // Not logged in - expanded state
  if (!userInfo) {
    return (
      <Button 
        variant="outline" 
        size="sm"
        onClick={handleLogin}
        disabled={isLoading}
        className={className}
      >
        {isLoading ? (
          "Connecting..."
        ) : (
          <>
            <img 
              src="https://www.google.com/favicon.ico" 
              alt="Google" 
              className="w-4 h-4 mr-2"
            />
            Sign in with Google
          </>
        )}
      </Button>
    )
  }

  // Logged in - expanded state
  return (
    <div className="flex flex-col gap-2">
      <button 
        onClick={handleSwitchAccount}
        disabled={isLoading}
        className="flex items-center gap-2 p-2 rounded-lg bg-muted w-full hover:bg-accent"
      >
        <img 
          src={userInfo.picture}
          alt={userInfo.name} 
          className="w-8 h-8 rounded-full"
        />
        <div className="flex-1 min-w-0 text-left">
          <div className="text-sm font-medium truncate">
            {userInfo.name}
          </div>
          <div className="text-xs text-muted-foreground truncate">
            {userInfo.email}
          </div>
        </div>
      </button>
      <Button 
        variant="ghost" 
        size="sm"
        onClick={handleLogout}
        disabled={isLoading}
        className="w-full justify-start"
      >
        <LogOut className="w-4 h-4 mr-2" />
        Sign out
      </Button>
    </div>
  )
} 