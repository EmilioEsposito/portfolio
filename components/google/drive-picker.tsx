'use client'

import { Button } from "@/components/ui/button"
import { useToast } from "@/components/ui/use-toast"
import { useRouter } from "next/navigation"
import { useCallback, useEffect, useState } from "react"

declare global {
  interface Window {
    gapi: any
    google: {
      accounts: {
        oauth2: {
          initTokenClient: (config: any) => any
        }
      }
      picker: {
        View: any
        ViewId: {
          DOCS: any
        }
        PickerBuilder: any
        Action: {
          PICKED: any
        }
        Feature: {
          MULTISELECT_ENABLED: any
        }
      }
    }
  }
}

interface DrivePickerProps {
  onSelect?: (files: Array<{id: string, name: string, mimeType: string}>) => void
  buttonText?: string
  allowMultiple?: boolean
  mimeTypes?: string[]
}

export default function DrivePicker({
  onSelect,
  buttonText = "Select from Drive",
  allowMultiple = false,
  mimeTypes = ['application/vnd.google-apps.spreadsheet', 'application/vnd.google-apps.document']
}: DrivePickerProps) {
  const { toast } = useToast()
  const router = useRouter()
  const [isLoading, setIsLoading] = useState(false)
  const [pickerInited, setPickerInited] = useState(false)
  const [tokenClient, setTokenClient] = useState<any>(null)

  // Load the Google API client library
  useEffect(() => {
    const loadGapiScript = () => {
      const script = document.createElement('script')
      script.src = 'https://apis.google.com/js/api.js'
      script.onload = () => {
        window.gapi.load('picker', () => setPickerInited(true))
      }
      document.body.appendChild(script)
    }

    const loadGisScript = () => {
      const script = document.createElement('script')
      script.src = 'https://accounts.google.com/gsi/client'
      script.onload = () => {
        // Initialize token client
        const client = window.google.accounts.oauth2.initTokenClient({
          client_id: process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID,
          scope: 'https://www.googleapis.com/auth/drive.file',
          callback: () => {}  // Will be set later
        })
        setTokenClient(client)
      }
      document.body.appendChild(script)
    }

    loadGapiScript()
    loadGisScript()
  }, [])

  const createPicker = useCallback((token: string) => {
    if (!window.google || !window.gapi?.client?.picker?.PickerBuilder) {
      console.error('Picker API not loaded')
      return
    }

    const view = new window.google.picker.View(window.google.picker.ViewId.DOCS)
    view.setMimeTypes(mimeTypes.join(','))

    const picker = new window.google.picker.PickerBuilder()
      .addView(view)
      .setOAuthToken(token)
      .setDeveloperKey(process.env.NEXT_PUBLIC_GOOGLE_API_KEY)
      .setCallback((data: any) => {
        if (data.action === window.google.picker.Action.PICKED) {
          const files = data.docs.map((doc: any) => ({
            id: doc.id,
            name: doc.name,
            mimeType: doc.mimeType
          }))
          onSelect?.(allowMultiple ? files : [files[0]])
        }
      })
      
    if (allowMultiple) {
      picker.enableFeature(window.google.picker.Feature.MULTISELECT_ENABLED)
    }
      
    picker.build().setVisible(true)
  }, [allowMultiple, mimeTypes, onSelect])

  const showPicker = async () => {
    setIsLoading(true)
    try {
      // Check if user is authenticated
      const authCheck = await fetch('/api/google/auth/check')
      const authData = await authCheck.json()
      
      if (!authData.authenticated) {
        toast({
          title: "Authentication Required",
          description: "Please connect your Google account first"
        })
        router.push('/auth/google')
        return
      }

      // Get access token
      const tokenResponse = await fetch('/api/google/auth/token')
      const { access_token } = await tokenResponse.json()
      
      if (!access_token) {
        throw new Error('Failed to get access token')
      }

      createPicker(access_token)
      
    } catch (error) {
      console.error('Failed to show picker:', error)
      toast({
        title: "Error",
        description: "Failed to open Drive picker",
        variant: "destructive"
      })
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <Button
      onClick={showPicker}
      disabled={isLoading || !pickerInited}
    >
      {isLoading ? "Loading..." : buttonText}
    </Button>
  )
} 