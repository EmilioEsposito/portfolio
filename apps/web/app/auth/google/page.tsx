'use client'

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useToast } from "@/components/ui/use-toast"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useEffect, useState } from "react"
import { CheckCircle2 } from "lucide-react"

function GoogleAuthContent() {
  const { toast } = useToast()
  const router = useRouter()
  const searchParams = useSearchParams()
  const [isLoading, setIsLoading] = useState(false)
  const [currentUser, setCurrentUser] = useState<string | null>(null)
  const forceAuth = searchParams.get('force') === 'true'

  // Check if already authenticated
  useEffect(() => {
    const checkAuth = async () => {
      try {
        const response = await fetch('/api/google/auth/check')
        const data = await response.json()
        
        if (data.authenticated && !forceAuth) {
          setCurrentUser(data.user_id)
        }
      } catch (error) {
        console.error('Auth check failed:', error)
      }
    }
    
    checkAuth()
  }, [forceAuth])

  const handleAuth = async () => {
    setIsLoading(true)
    try {
      // Generate a random state value
      const state = Math.random().toString(36).substring(2)
      
      // Get auth URL with state parameter
      const response = await fetch('/api/google/auth/url', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ state })
      })
      const data = await response.json()
      
      // Store state in sessionStorage for verification after redirect
      sessionStorage.setItem('oauth_state', state)
      
      // Add prompt=select_account to force account selection
      const authUrl = new URL(data.url)
      authUrl.searchParams.set('prompt', 'select_account')
      
      // Redirect to Google OAuth
      window.location.href = authUrl.toString()
      
    } catch (error) {
      console.error('Failed to get auth URL:', error)
      toast({
        title: "Error",
        description: "Failed to start authentication process",
        variant: "destructive"
      })
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="container flex items-center justify-center min-h-screen py-10">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>
            {currentUser ? "Google Account Connected" : "Google Authentication"}
          </CardTitle>
          <CardDescription>
            {currentUser 
              ? "Your Google account is connected and ready to use"
              : "Sign in with Google to access Drive and Gmail features"}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {currentUser && (
              <div className="flex items-center gap-2 p-3 bg-muted rounded-lg">
                <CheckCircle2 className="h-5 w-5 text-green-500" />
                <span className="flex-1 text-sm font-medium">{currentUser}</span>
                <Button 
                  variant="outline" 
                  size="sm"
                  onClick={handleAuth}
                >
                  Switch Account
                </Button>
              </div>
            )}
            <p className="text-sm text-muted-foreground">
              This will allow the app to:
            </p>
            <ul className="list-disc list-inside text-sm text-muted-foreground space-y-2">
              <li>Access files you select in Google Drive</li>
              <li>Read your Gmail messages and labels</li>
              <li>Send emails on your behalf</li>
            </ul>
            {!currentUser && (
              <Button 
                className="w-full"
                onClick={handleAuth}
                disabled={isLoading}
              >
                {isLoading ? "Connecting..." : "Connect with Google"}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

export default function GoogleAuthPage() {
  return (
    <Suspense fallback={
      <div className="container flex items-center justify-center min-h-screen py-10">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>Loading...</CardTitle>
            <CardDescription>Please wait while we set up authentication</CardDescription>
          </CardHeader>
        </Card>
      </div>
    }>
      <GoogleAuthContent />
    </Suspense>
  )
} 