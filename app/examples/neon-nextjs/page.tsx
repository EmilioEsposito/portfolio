"use client"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useState, useEffect } from "react"

interface Example {
  id: number
  title: string
  content: string
  created_at: string
}

export default function NeonNextJSExample() {
  const [examples, setExamples] = useState<Example[]>([])
  const [newTitle, setNewTitle] = useState("")
  const [newContent, setNewContent] = useState("")
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetchExamples()
  }, [])

  const fetchExamples = async () => {
    try {
      const response = await fetch("/api/examples")
      if (!response.ok) {
        const errorData = await response.json()
        console.error('Server error details:', errorData)
        throw new Error(`HTTP error! status: ${response.status}, details: ${JSON.stringify(errorData)}`)
      }
      const data = await response.json()
      if (Array.isArray(data)) {
        // Sort by created_at in descending order (newest first)
        const sortedExamples = data.sort((a, b) => 
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        )
        setExamples(sortedExamples)
      } else {
        console.error("Unexpected response format:", data)
      }
    } catch (error) {
      console.error("Error fetching examples:", error)
    }
  }

  const addExample = async () => {
    if (!newTitle.trim() || !newContent.trim()) return

    setLoading(true)
    try {
      const response = await fetch("/api/examples", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ title: newTitle, content: newContent }),
      })
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }
      const data = await response.json()
      if (data && data.id && data.title && data.content) {
        setExamples([data, ...examples])
        setNewTitle("")
        setNewContent("")
      } else {
        console.error("Unexpected response format:", data)
      }
    } catch (error) {
      console.error("Error adding example:", error)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="container mx-auto py-10 px-4 md:px-8">
      <h1 className="text-4xl font-bold mb-8">Next.js + Neon Postgres Example</h1>
      <Card>
        <CardHeader>
          <CardTitle>Examples</CardTitle>
          <CardDescription>Using Next.js API routes with Neon Postgres</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-4">
            <div className="grid w-full gap-2">
              <Input
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="Enter a title..."
              />
              <Textarea
                value={newContent}
                onChange={(e) => setNewContent(e.target.value)}
                placeholder="Enter content..."
                rows={3}
              />
              <div className="flex space-x-2">
                <Button onClick={addExample} disabled={loading}>
                  Add
                </Button>
                <Button variant="outline" onClick={fetchExamples}>
                  Refresh
                </Button>
              </div>
            </div>
          </div>
          <div className="space-y-4">
            {examples.map((example) => (
              <div
                key={example.id}
                className="p-4 rounded-lg border bg-card text-card-foreground shadow-sm"
              >
                <h3 className="font-semibold mb-2">{example.title}</h3>
                <p className="whitespace-pre-wrap mb-2">{example.content}</p>
                <p className="text-sm text-muted-foreground">
                  {new Date(example.created_at).toLocaleString()}
                </p>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
} 