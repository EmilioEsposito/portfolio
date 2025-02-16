'use client'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import DrivePicker from "@/components/google/drive-picker"
import { useToast } from "@/components/ui/use-toast"
import { H1, H2, P, Lead } from "@/components/typography"
import { PageContainer } from "@/components/page-container"

export default function GoogleDrivePickerPage() {
  const { toast } = useToast()

  const handleSpreadsheetSelect = (files: Array<{id: string, name: string, mimeType: string}>) => {
    toast({
      title: "Spreadsheet Selected",
      description: `Selected ${files.length} file(s): ${files.map(f => f.name).join(', ')}`
    })
    console.log('Selected files:', files)
  }

  const handleDocumentSelect = (files: Array<{id: string, name: string, mimeType: string}>) => {
    toast({
      title: "Document Selected",
      description: `Selected ${files.length} file(s): ${files.map(f => f.name).join(', ')}`
    })
    console.log('Selected files:', files)
  }

  const handleAnyFileSelect = (files: Array<{id: string, name: string, mimeType: string}>) => {
    toast({
      title: "Files Selected",
      description: `Selected ${files.length} file(s): ${files.map(f => f.name).join(', ')}`
    })
    console.log('Selected files:', files)
  }

  return (
    <PageContainer className="space-y-6">
      <H1>Google Drive Integration</H1>
      <Lead>Access and manage your Google Drive files directly from the application.</Lead>
      
      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        {/* Spreadsheet Picker */}
        <Card>
          <CardHeader>
            <CardTitle>Select Spreadsheet</CardTitle>
            <CardDescription>
              Choose a single Google Spreadsheet from your Drive
            </CardDescription>
          </CardHeader>
          <CardContent>
            <DrivePicker
              buttonText="Select Spreadsheet"
              mimeTypes={['application/vnd.google-apps.spreadsheet']}
              allowMultiple={false}
              onSelect={handleSpreadsheetSelect}
            />
          </CardContent>
        </Card>

        {/* Document Picker */}
        <Card>
          <CardHeader>
            <CardTitle>Select Documents</CardTitle>
            <CardDescription>
              Choose multiple Google Docs from your Drive
            </CardDescription>
          </CardHeader>
          <CardContent>
            <DrivePicker
              buttonText="Select Documents"
              mimeTypes={['application/vnd.google-apps.document']}
              allowMultiple={true}
              onSelect={handleDocumentSelect}
            />
          </CardContent>
        </Card>

        {/* Any File Picker */}
        <Card>
          <CardHeader>
            <CardTitle>Select Any Files</CardTitle>
            <CardDescription>
              Choose any type of files from your Drive
            </CardDescription>
          </CardHeader>
          <CardContent>
            <DrivePicker
              buttonText="Select Files"
              mimeTypes={[]} // Empty array allows all file types
              allowMultiple={true}
              onSelect={handleAnyFileSelect}
            />
          </CardContent>
        </Card>
      </div>

      <div className="mt-8">
        <H2>Usage Instructions</H2>
        <ol className="my-6 ml-6 list-disc [&>li]:mt-2">
          <li><P>Click any of the buttons above to open the Google Drive picker</P></li>
          <li><P>If not signed in, you&apos;ll be redirected to authenticate with Google</P></li>
          <li><P>Select the file(s) you want from your Google Drive</P></li>
          <li><P>The selected file information will appear in a toast notification</P></li>
          <li><P>Check the browser console for the complete file metadata</P></li>
        </ol>

        <H2>Features</H2>
        <ul className="my-6 ml-6 list-disc [&>li]:mt-2">
          <li><P>Single or multiple file selection</P></li>
          <li><P>File type filtering (Spreadsheets, Documents, or any file type)</P></li>
          <li><P>Automatic authentication handling</P></li>
          <li><P>Toast notifications for user feedback</P></li>
          <li><P>Responsive grid layout</P></li>
        </ul>
      </div>
    </PageContainer>
  )
} 