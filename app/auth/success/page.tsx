'use client'

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { CheckCircle } from "lucide-react"
import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"

export default function AuthSuccessPage() {
  const router = useRouter()
  const [userInfo, setUserInfo] = useState<{
    authenticated: boolean
    user_id?: string
    scopes?: string[]
  } | null>(null)

  useEffect(() => {
    const checkAuth = async () => {
      try {
        const response = await fetch('/api/google/auth/check')
        const data = await response.json()
        setUserInfo(data)
        
        if (!data.authenticated) {
          router.push('/auth/google')
        }
      } catch (error) {
        console.error('Auth check failed:', error)
        router.push('/auth/google')
      }
    }
    
    checkAuth()
  }, [router])

  if (!userInfo?.authenticated) {
    return null
  }

  return (
    <div className="container flex items-center justify-center min-h-screen py-10">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="flex justify-center mb-4">
            <CheckCircle className="w-12 h-12 text-green-500" />
          </div>
          <CardTitle>Authentication Successful</CardTitle>
          <CardDescription>
            You have successfully connected your Google account
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="p-4 bg-muted rounded-lg">
              <p className="font-medium">Connected Account</p>
              <p className="text-sm text-muted-foreground">{userInfo.user_id}</p>
            </div>
            <div className="space-y-2">
              <p className="text-sm font-medium">Granted Permissions:</p>
              <ul className="list-disc list-inside text-sm text-muted-foreground space-y-1">
                {userInfo.scopes?.map((scope, index) => (
                  <li key={index}>
                    {scope.replace('https://www.googleapis.com/auth/', '')}
                  </li>
                ))}
              </ul>
            </div>
            <div className="flex space-x-4">
              <Button 
                className="flex-1"
                onClick={() => router.push('/dashboard')}
              >
                Go to Dashboard
              </Button>
              <Button 
                variant="outline"
                className="flex-1"
                onClick={() => router.push('/auth/google')}
              >
                Reconnect
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
} 