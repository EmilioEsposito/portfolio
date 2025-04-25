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
  const [pickerApiLoaded, setPickerApiLoaded] = useState(false)
  const [tokenClient, setTokenClient] = useState<any>(null)

  // Load the Google API client libraries
  useEffect(() => {
    const loadGapiAndPicker = () => {
      return new Promise<void>((resolve, reject) => {
        // First load the gapi script
        const script = document.createElement('script')
        script.src = 'https://apis.google.com/js/api.js'
        script.onload = () => {
          // Load both picker and drive APIs
          window.gapi.load('client:picker', {
            callback: async () => {
              try {
                // Initialize the client with your API key
                await window.gapi.client.init({
                  apiKey: process.env.NEXT_PUBLIC_GOOGLE_DRIVE_PICKER_API_KEY,
                  discoveryDocs: ['https://www.googleapis.com/discovery/v1/apis/drive/v3/rest']
                })
                console.log('Picker API loaded successfully')
                setPickerApiLoaded(true)
                resolve()
              } catch (error) {
                console.error('Error initializing GAPI client:', error)
                reject(error)
              }
            },
            onerror: () => {
              const error = 'Failed to load Picker API'
              console.error(error)
              reject(new Error(error))
            }
          })
        }
        script.onerror = (error: unknown) => {
          console.error('Failed to load Google APIs:', error)
          reject(new Error('Failed to load Google APIs'))
        }
        document.body.appendChild(script)
      })
    }

    const loadGisScript = () => {
      return new Promise<void>((resolve, reject) => {
        // Log environment variables status
        console.log('Google Client Environment check:', {
          clientIdDefined: !!process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID,
          clientIdValue: process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID?.substring(0, 5) + '...'
        })

        if (!process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID) {
          const error = 'Google Client ID is not defined'
          console.error(error)
          toast({
            title: "Configuration Error",
            description: "Google Client ID is missing. Please check your environment variables.",
            variant: "destructive"
          })
          reject(new Error(error))
          return
        }

        const script = document.createElement('script')
        script.src = 'https://accounts.google.com/gsi/client'
        script.onload = () => {
          // Initialize token client with correct scopes
          try {
            const client = window.google.accounts.oauth2.initTokenClient({
              client_id: process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID,
              scope: [
                'https://www.googleapis.com/auth/drive.file',
                'https://www.googleapis.com/auth/drive.readonly',
                'https://www.googleapis.com/auth/drive.metadata.readonly',
                'https://www.googleapis.com/auth/userinfo.profile',
                'https://www.googleapis.com/auth/userinfo.email'
              ].join(' '),
              callback: () => {}  // Will be set later
            })
            setTokenClient(client)
            resolve()
          } catch (error) {
            console.error('Failed to initialize token client:', error)
            toast({
              title: "Error",
              description: "Failed to initialize Google authentication",
              variant: "destructive"
            })
            reject(error)
          }
        }
        script.onerror = (error) => {
          console.error('Failed to load Google GSI script:', error)
          reject(error)
        }
        document.body.appendChild(script)
      })
    }

    // Load both scripts
    Promise.all([loadGapiAndPicker(), loadGisScript()])
      .catch(error => {
        console.error('Failed to load Google APIs:', error)
        toast({
          title: "Error",
          description: "Failed to initialize Google Drive Picker",
          variant: "destructive"
        })
      })

    // Cleanup function
    return () => {
      // Remove scripts on unmount
      const scripts = document.querySelectorAll('script[src*="googleapis.com"]')
      scripts.forEach(script => script.remove())
    }
  }, [toast])

  const createPicker = useCallback((token: string) => {
    // Log environment variable status
    console.log('Environment check:', {
      isDefined: !!process.env.NEXT_PUBLIC_GOOGLE_DRIVE_PICKER_API_KEY,
      value: process.env.NEXT_PUBLIC_GOOGLE_DRIVE_PICKER_API_KEY?.substring(0, 5) + '...' // Only log first 5 chars for security
    })

    if (!process.env.NEXT_PUBLIC_GOOGLE_DRIVE_PICKER_API_KEY) {
      console.error('Google Drive Picker API key is not defined')
      toast({
        title: "Configuration Error",
        description: "Google Drive Picker API key is missing. Please check your environment variables.",
        variant: "destructive"
      })
      return
    }

    if (!window.google?.picker) {
      console.error('Picker API not loaded')
      toast({
        title: "Error",
        description: "Google Drive Picker not initialized. Please try again.",
        variant: "destructive"
      })
      return
    }

    try {
      // Clean and validate API key
      const apiKey = process.env.NEXT_PUBLIC_GOOGLE_DRIVE_PICKER_API_KEY.trim()
      
      // Create the picker
      const view = new window.google.picker.View(window.google.picker.ViewId.DOCS)
      view.setMimeTypes(mimeTypes.join(','))

      const picker = new window.google.picker.PickerBuilder()
        .addView(view)
        .setOAuthToken(token)
        .setDeveloperKey(apiKey)
        .setOrigin(window.location.protocol + '//' + window.location.host)
        .setAppId(process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID?.split('-')[0]) // Extract app ID from client ID
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

    } catch (error) {
      console.error('Failed to create picker:', error)
      toast({
        title: "Error",
        description: "Failed to create Google Drive Picker",
        variant: "destructive"
      })
    }
  }, [allowMultiple, mimeTypes, onSelect, toast])

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
      disabled={isLoading || !pickerApiLoaded}
    >
      {isLoading ? "Loading..." : buttonText}
    </Button>
  )
} 