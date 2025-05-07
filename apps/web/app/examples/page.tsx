import Link from "next/link"
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

const examples = [
  {
    title: "Protected Endpoint with Clerk Auth",
    description: "Shows how to protect FastAPI endpoints with Clerk authentication and access them from Next.js. Features JWT token handling, error states, and user data display.",
    href: "/examples/protected",
    tags: ["FastAPI", "Clerk", "Authentication", "Next.js"]
  },
  // {
  //   title: "Google Drive File Picker Integration",
  //   description: "Demonstrates integrating Google Drive File Picker with Next.js. Features file selection, type filtering, and OAuth authentication handling.",
  //   href: "/examples/google-drive-picker",
  //   tags: ["Next.js", "Google Drive API", "OAuth", "TypeScript"]
  // },
  {
    title: "FastAPI + GraphQL + Neon Postgres Example",
    description: "Demonstrates using FastAPI with GraphQL and Neon Postgres. Shows how to handle GraphQL queries, mutations, and field mapping.",
    href: "/examples/neon-fastapi",
    tags: ["FastAPI", "GraphQL", "Neon", "Postgres"]
  },
  {
    title: "Next.js + Neon Postgres Example",
    description: "Shows how to use Next.js API routes with Neon's serverless driver for Postgres. Includes error handling and response validation.",
    href: "/examples/neon-nextjs",
    tags: ["Next.js", "Neon", "Postgres", "REST"]
  },
  {
    title: "Multi-Select Component",
    description: "A reusable multi-select component built with Shadcn UI and Radix UI. Features search, keyboard navigation, and custom styling.",
    href: "/examples/multi-select",
    tags: ["React", "Shadcn UI", "Radix UI", "TypeScript"]
  },
  {
    title: "Shared React Native Component (Expo + Next.js)",
    description: "Demonstrates rendering a basic component defined in a shared package (`@portfolio/features`) within both an Expo app and a Next.js web page.",
    href: "/examples/react-native-shared",
    tags: ["React Native", "Expo", "Next.js", "Monorepo", "Shared Code"]
  }
]

export default function ExamplesPage() {
  return (
    <div className="container mx-auto py-10 px-4 md:px-8">
      <div className="max-w-3xl mb-12">
        <h1 className="text-4xl font-bold mb-4">Examples</h1>
        <p className="text-lg text-muted-foreground mb-2">
          This section showcases minimal, focused examples that demonstrate specific technical concepts or integrations. Think of them as "hello world" examples that you can learn from and build upon.
        </p>
        <p className="text-lg text-muted-foreground">
          Each example is intentionally simple and stripped of business logic, making it easier to understand the core technical concepts. You can use these as building blocks when implementing more complex features with real business requirements.
        </p>
      </div>
      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        {examples.map((example) => (
          <Link 
            key={example.href} 
            href={example.href}
            className="transition-transform hover:scale-[1.02] active:scale-[0.98]"
          >
            <Card className="h-full">
              <CardHeader>
                <CardTitle>{example.title}</CardTitle>
                <CardDescription>{example.description}</CardDescription>
                <div className="flex flex-wrap gap-2 mt-2">
                  {example.tags.map((tag) => (
                    <span
                      key={tag}
                      className="inline-flex items-center rounded-md bg-muted px-2 py-1 text-xs font-medium ring-1 ring-inset ring-muted"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </CardHeader>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  )
} 