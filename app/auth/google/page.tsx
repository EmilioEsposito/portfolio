'use client'

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useToast } from "@/components/ui/use-toast"
import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"

export default function GoogleAuthPage() {
  const { toast } = useToast()
  const router = useRouter()
  const [isLoading, setIsLoading] = useState(false)

  // Check if already authenticated
  useEffect(() => {
    const checkAuth = async () => {
      try {
        const response = await fetch('/api/google/auth/check')
        const data = await response.json()
        
        if (data.authenticated) {
          toast({
            title: "Already authenticated",
            description: "You are already signed in with Google. Redirecting home."
          })
          router.push('/')
        }
      } catch (error) {
        console.error('Auth check failed:', error)
      }
    }
    
    checkAuth()
  }, [router, toast])

  const handleAuth = async () => {
    setIsLoading(true)
    try {
      const response = await fetch('/api/google/auth/url')
      const data = await response.json()
      
      // Redirect to Google OAuth
      window.location.href = data.url
      
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
          <CardTitle>Google Authentication</CardTitle>
          <CardDescription>
            Sign in with Google to access Drive and Gmail features
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              This will allow the app to:
            </p>
            <ul className="list-disc list-inside text-sm text-muted-foreground space-y-2">
              <li>Access files you select in Google Drive</li>
              <li>Read your Gmail messages and labels</li>
              <li>Send emails on your behalf</li>
            </ul>
            <Button 
              className="w-full"
              onClick={handleAuth}
              disabled={isLoading}
            >
              {isLoading ? "Connecting..." : "Connect with Google"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
} 