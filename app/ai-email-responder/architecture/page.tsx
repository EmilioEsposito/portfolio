"use client";

import Link from "next/link";
import Mermaid from "@/components/ui/mermaid";

export default function AIEmailResponderArchitecture() {
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
    <div className="container mx-auto py-8 px-4 sm:px-6 space-y-8">
      <div className="space-y-4">
        <div className="flex items-baseline gap-4">
          <h1 className="text-3xl font-bold">AI Email Responder Architecture</h1>
          <Link 
            href="/ai-email-responder"
            className="text-blue-500 hover:text-blue-600 hover:underline text-sm"
          >
            ← Back to Preview Tool
          </Link>
        </div>

        <div className="prose dark:prose-invert max-w-none">
          <h2 className="text-2xl font-semibold mt-8 mb-4">Overview</h2>
          <p className="text-lg mb-6">
            The AI Email Responder is designed to automatically respond to Zillow rental inquiries in real-time. 
            This page outlines the production architecture and how different components interact to create a 
            seamless automated response system.
          </p>

          <h2 className="text-2xl font-semibold mt-8 mb-4">Architecture Diagram</h2>
          <p className="text-lg mb-6">
            The diagram below illustrates how these components interact in the production environment. 
            The flow starts when a new Zillow inquiry arrives in Gmail and ends with the AI agent potentially 
            sending an automated response.
          </p>
        </div>

        <div className="border rounded-lg p-8 bg-white dark:bg-zinc-950 my-8">
          <Mermaid chart={diagram} />
        </div>

        <div className="prose dark:prose-invert max-w-none">
          <h2 className="text-2xl font-semibold mt-8 mb-4">System Components</h2>
          
          <h3 className="text-xl font-medium mt-4 mb-2">1. Gmail Integration</h3>
          <ul className="list-disc pl-6 space-y-1 mb-4">
            <li>Gmail inbox monitors for new Zillow rental inquiry emails</li>
            <li>When a new email arrives, Gmail triggers a notification to Google PubSub</li>
          </ul>

          <h3 className="text-xl font-medium mt-4 mb-2">2. Google Cloud Platform</h3>
          <ul className="list-disc pl-6 space-y-1 mb-4">
            <li>A PubSub Topic receives Gmail notifications</li>
            <li>A PubSub Subscription processes these notifications and triggers our FastAPI endpoint</li>
            <li>System Instructions are stored in a Google Doc for easy editing by non-technical users</li>
          </ul>

          <h3 className="text-xl font-medium mt-4 mb-2">3. Backend Services</h3>
          <ul className="list-disc pl-6 space-y-1 mb-4">
            <li>FastAPI endpoint receives PubSub notifications</li>
            <li>Retrieves email content using Gmail API</li>
            <li>Fetches current System Instructions from Google Doc</li>
            <li>Constructs final prompt and calls OpenAI API</li>
          </ul>

          <h3 className="text-xl font-medium mt-4 mb-2">4. AI Agent</h3>
          <ul className="list-disc pl-6 space-y-1 mb-4">
            <li>GPT-4 model processes the email with provided instructions</li>
            <li>Has access to a "Send Email" tool function</li>
            <li>Can decide whether to send an automated response</li>
            <li>If appropriate, generates and sends response in real-time</li>
          </ul>

    

          <h2 className="text-2xl font-semibold mt-8 mb-4">Customization</h2>
          <p className="text-lg mb-6">
            The System Instructions stored in Google Docs can be updated at any time. Changes take effect immediately
            for all new incoming emails. This allows for quick adjustments to the AI's response style without requiring
            any code changes or deployments.
          </p>
        </div>
      </div>

      {/* Footnote */}
      <div className="text-sm text-muted-foreground italic text-center border-t pt-4">
        This documentation and architecture diagram were created with Cursor AI (Claude 3.5 Sonnet Model)
      </div>
    </div>
  );
} 