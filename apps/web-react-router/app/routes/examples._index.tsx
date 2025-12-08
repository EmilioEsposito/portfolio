import { Link } from "react-router";
import type { Route } from "./+types/examples._index";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Examples | Emilio Esposito" },
    {
      name: "description",
      content: "Technical examples and demos showcasing specific integrations",
    },
  ];
}

const examples = [
  {
    title: "Protected Endpoint with Clerk Auth",
    description:
      "Shows how to protect FastAPI endpoints with Clerk authentication and access them from React Router. Features JWT token handling, error states, and user data display.",
    href: "/examples/protected",
    tags: ["FastAPI", "Clerk", "Authentication", "React Router"],
  },
  {
    title: "FastAPI + GraphQL + Neon Postgres",
    description:
      "Demonstrates using FastAPI with GraphQL and Neon Postgres. Shows how to handle GraphQL queries, mutations, and field mapping.",
    href: "/examples/neon-fastapi",
    tags: ["FastAPI", "GraphQL", "Neon", "Postgres"],
  },
  {
    title: "Multi-Select Component",
    description:
      "A reusable multi-select component built with Shadcn UI and Radix UI. Features search, keyboard navigation, and custom styling.",
    href: "/examples/multi-select",
    tags: ["React", "Shadcn UI", "Radix UI", "TypeScript"],
  },
  {
    title: "PydanticAI Email Approval",
    description:
      "Human-in-the-loop email approval workflow using PydanticAI deferred tools. Demonstrates how to pause agent execution for human oversight before sending emails.",
    href: "/examples/email-approval",
    tags: ["PydanticAI", "FastAPI", "Human-in-the-Loop", "Deferred Tools"],
  },
];

export default function ExamplesPage() {
  return (
    <div className="container mx-auto py-10 px-4 md:px-8">
      <div className="max-w-3xl mb-12">
        <h1 className="text-4xl font-bold mb-4">Examples</h1>
        <p className="text-lg text-muted-foreground mb-2">
          This section showcases minimal, focused examples that demonstrate
          specific technical concepts or integrations. Think of them as "hello
          world" examples that you can learn from and build upon.
        </p>
        <p className="text-lg text-muted-foreground">
          Each example is intentionally simple and stripped of business logic,
          making it easier to understand the core technical concepts. You can
          use these as building blocks when implementing more complex features
          with real business requirements.
        </p>
      </div>
      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        {examples.map((example) => (
          <Link
            key={example.href}
            to={example.href}
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
  );
}
