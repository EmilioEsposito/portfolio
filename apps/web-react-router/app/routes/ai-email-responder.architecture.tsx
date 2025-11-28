import type { Route } from "./+types/ai-email-responder.architecture";
import { Link } from "react-router";
import Mermaid from "~/components/ui/mermaid";
import { H1, H2, H3, P } from "~/components/typography";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "AI Email Responder Architecture | Emilio Esposito" },
    {
      name: "description",
      content: "System architecture for the AI-powered email responder",
    },
  ];
}

export default function AIEmailResponderArchitecturePage() {
  const diagram = `
  flowchart TD
    subgraph Gmail[Gmail Inbox]
      email[Zillow Inquiry Email]
    end

    subgraph GCP[Google Cloud Platform]
      topic[PubSub Topic]
      sub[PubSub Subscription]
    end

    subgraph Backend[Backend Services]
      api[FastAPI Endpoint]
      prompt[Construct Prompt]
      ai[OpenAI GPT-4]
      doc[System Instructions<br/>Google Doc]
    end

    email --> topic
    topic --> sub
    sub --> api
    api --> |Email Content| prompt
    api --> doc
    doc --> |Instructions| prompt
    prompt --> ai
    ai --> |Send Response| email

    classDef primary fill:#4f46e5,stroke:#4f46e5,color:#fff
    classDef secondary fill:#f3f4f6,stroke:#e5e7eb
    classDef highlight fill:#3b82f6,stroke:#3b82f6,color:#fff

    class email,topic,sub secondary
    class api,prompt,ai primary
    class doc highlight
  `;

  return (
    <div className="container mx-auto px-4 py-8 max-w-4xl">
      <div className="space-y-4">
        <div className="flex items-baseline gap-4">
          <H1>AI Email Responder Architecture</H1>
          <Link
            to="/ai-email-responder"
            className="text-blue-500 hover:text-blue-600 hover:underline text-sm"
          >
            ← Back to Preview Tool
          </Link>
        </div>

        <div className="space-y-8">
          <div>
            <H2>Overview</H2>
            <P className="mt-4">
              The AI Email Responder is designed to automatically respond to
              Zillow rental inquiries in real-time. This page outlines the
              production architecture and how different components interact to
              create a seamless automated response system.
            </P>
          </div>

          <div>
            <H2>Architecture Diagram</H2>
            <P className="mt-4">
              The diagram below illustrates how these components interact in the
              production environment. The flow starts when a new Zillow inquiry
              arrives in Gmail and ends with the AI agent potentially sending an
              automated response.
            </P>

            <div className="border rounded-lg p-8 bg-white dark:bg-zinc-950 mt-6">
              <Mermaid chart={diagram} />
            </div>
          </div>

          <div>
            <H2>System Components</H2>

            <div className="mt-6 space-y-6">
              <div>
                <H3>1. Gmail Integration</H3>
                <ul className="mt-2 list-disc pl-6 space-y-1 text-muted-foreground">
                  <li>
                    Gmail inbox monitors for new Zillow rental inquiry emails
                  </li>
                  <li>
                    When a new email arrives, Gmail triggers a notification to
                    Google PubSub
                  </li>
                </ul>
              </div>

              <div>
                <H3>2. Google Cloud Platform</H3>
                <ul className="mt-2 list-disc pl-6 space-y-1 text-muted-foreground">
                  <li>A PubSub Topic receives Gmail notifications</li>
                  <li>
                    A PubSub Subscription processes these notifications and
                    triggers our FastAPI endpoint
                  </li>
                  <li>
                    System Instructions are stored in a Google Doc for easy
                    editing by non-technical users
                  </li>
                </ul>
              </div>

              <div>
                <H3>3. Backend Services</H3>
                <ul className="mt-2 list-disc pl-6 space-y-1 text-muted-foreground">
                  <li>FastAPI endpoint receives PubSub notifications</li>
                  <li>Retrieves email content using Gmail API</li>
                  <li>Fetches current System Instructions from Google Doc</li>
                  <li>Constructs final prompt and calls OpenAI API</li>
                </ul>
              </div>

              <div>
                <H3>4. AI Agent</H3>
                <ul className="mt-2 list-disc pl-6 space-y-1 text-muted-foreground">
                  <li>GPT-4 model processes the email with provided instructions</li>
                  <li>Has access to a "Send Email" tool function</li>
                  <li>Can decide whether to send an automated response</li>
                  <li>If appropriate, generates and sends response in real-time</li>
                </ul>
              </div>
            </div>
          </div>

          <div>
            <H2>Customization</H2>
            <P className="mt-4">
              The System Instructions stored in Google Docs can be updated at
              any time. Changes take effect immediately for all new incoming
              emails. This allows for quick adjustments to the AI's response
              style without requiring any code changes or deployments.
            </P>
          </div>
        </div>
      </div>

      {/* Footnote */}
      <div className="text-sm text-muted-foreground italic text-center border-t mt-12 pt-4">
        This documentation and architecture diagram were created with Cursor AI
        (Claude 3.5 Sonnet Model)
      </div>
    </div>
  );
}
