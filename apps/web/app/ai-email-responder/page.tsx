"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ReloadIcon } from "@radix-ui/react-icons";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import Link from "next/link";
interface ZillowEmail {
  id: string;
  subject: string;
  sender: string;
  received_at: string;
  body_html: string | null;
}

interface SystemInstruction {
  id: string;
  text: string;
}

export default function AIEmailResponder() {
  const [emails, setEmails] = useState<ZillowEmail[]>([]);
  const [selectedEmail, setSelectedEmail] = useState<ZillowEmail | null>(null);
  const [systemInstructions, setSystemInstructions] = useState<SystemInstruction[]>(() => {
    // Try to load saved instructions from localStorage
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('systemInstructions');
      if (saved) {
        try {
          return JSON.parse(saved);
        } catch (e) {
          console.error('Failed to parse saved instructions:', e);
        }
      }
    }
    // Default instruction if nothing in localStorage
    return [{
      id: "default",
      text: "You are a property manager. Be professional but friendly. Keep the response concise. Address their questions directly. Sign off as 'Property Management Team'."
    }];
  });
  
  // Save instructions to localStorage whenever they change
  useEffect(() => {
    localStorage.setItem('systemInstructions', JSON.stringify(systemInstructions));
  }, [systemInstructions]);

  const [newInstruction, setNewInstruction] = useState("");
  const [selectedInstruction, setSelectedInstruction] = useState<string>("default");
  const [generatedResponse, setGeneratedResponse] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isFetchingEmails, setIsFetchingEmails] = useState(false);

  // Fetch emails on component mount
  useEffect(() => {
    fetchEmails();
  }, []);

  async function fetchEmails() {
    setIsFetchingEmails(true);
    try {
      const response = await fetch("/api/google/gmail/get_zillow_emails");
      const data = await response.json();
      setEmails(data);
      setSelectedEmail(null); // Reset selection when fetching new emails
    } catch (error) {
      console.error("Failed to fetch emails:", error);
    } finally {
      setIsFetchingEmails(false);
    }
  }

  function addSystemInstruction() {
    if (!newInstruction.trim() || systemInstructions.length >= 10) return;
    
    const instruction: SystemInstruction = {
      id: crypto.randomUUID(),
      text: newInstruction.trim(),
    };
    
    setSystemInstructions([...systemInstructions, instruction]);
    setSelectedInstruction(instruction.id);
    setNewInstruction("");
  }

  function updateCurrentInstruction() {
    if (!newInstruction.trim() || !selectedInstruction) return;
    
    setSystemInstructions(prevInstructions => 
      prevInstructions.map(instruction => 
        instruction.id === selectedInstruction
          ? { ...instruction, text: newInstruction.trim() }
          : instruction
      )
    );
  }

  // Helper function to format instruction name
  function formatInstructionName(instruction: SystemInstruction, index: number): string {
    const previewLength = 20;
    const preview = instruction.text.length > previewLength 
      ? instruction.text.substring(0, previewLength) + "..."
      : instruction.text;
    return `#${index + 1} - ${preview}`;
  }

  async function generateResponse() {
    if (!selectedEmail || !selectedInstruction) return;
    
    setIsLoading(true);
    try {
      const selectedInstructionText = systemInstructions.find(
        (instruction) => instruction.id === selectedInstruction
      )?.text;

      if (!selectedInstructionText) {
        throw new Error("Selected instruction not found");
      }

      // Format the email content
      const emailContent = `Subject: ${selectedEmail.subject}
From: ${selectedEmail.sender}
Message:
${selectedEmail.body_html}`;

      const response = await fetch("/api/google/gmail/generate_email_response", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email_content: emailContent,
          system_instruction: selectedInstructionText,
        }),
      });

      if (!response.ok) {
        throw new Error(`Failed to generate response: ${response.statusText}`);
      }

      const data = await response.json();
      setGeneratedResponse(data.response);
    } catch (error) {
      console.error("Failed to generate response:", error);
      setGeneratedResponse("Failed to generate response. Please try again.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="container mx-auto py-8 px-4 sm:px-6 space-y-8">
      <div className="space-y-4">
        <div className="flex items-baseline justify-between">
          <h1 className="text-3xl font-bold">AI Email Responder Preview Tool</h1>
          <Link 
            href="/ai-email-responder/architecture"
            className="text-sm text-muted-foreground hover:text-blue-500 flex items-center gap-2 group"
          >
            View System Architecture
            <span className="group-hover:translate-x-0.5 transition-transform">→</span>
          </Link>
        </div>
        <div className="prose dark:prose-invert max-w-none">
          <p className="text-muted-foreground">
            This tool helps test and refine AI agent instructions for automated Zillow rental inquiry responses. The workflow is simple:
          </p>
          <ol className="text-muted-foreground list-decimal list-inside space-y-1">
            <li>Select a sample email from real Zillow inquiries</li>
            <li>Create or modify AI agent instructions to define the response style</li>
            <li>Click Generate to preview how the AI would respond</li>
          </ol>
          <p className="text-muted-foreground mt-4">
            Once the optimal instructions are determined, they'll be used in production where Google PubSub 
            notifications will call the{" "}
            <Link 
              href="api/docs#/google/handle_gmail_notifications_api_google_pubsub_gmail_notifications_post"
              className="text-blue-500 hover:text-blue-600 hover:underline font-medium"
            >
              FastAPI /api/google/pubsub/gmail/notifications endpoint
            </Link>
            {" "}to automatically respond to incoming Zillow inquiries in real-time.
          </p>
          <div className="text-muted-foreground mt-4">
            For more information on the final production implementation, see the{" "}
            <Link 
              href="/ai-email-responder/architecture" 
              className="text-blue-500 hover:text-blue-600 hover:underline font-medium"
            >
              System Architecture
            </Link>
            {" "}page.
          </div>
        </div>
      </div>
      
      {/* Main Grid Layout */}
      <div className="grid lg:grid-cols-[1fr,1fr] gap-8">
        {/* Email Selection Container */}
        <div className="space-y-2">
          <h2 className="text-xl font-semibold px-1">Incoming Zillow Inquiries</h2>
          <div className="border rounded-lg grid grid-cols-[auto,1fr]">
            {/* Email Tabs */}
            <div className="border-r bg-muted/50">
              <div className="sticky top-0 p-2 border-b bg-muted flex items-center justify-between">
                <h2 className="text-sm font-medium">Sample Emails</h2>
                <Button 
                  variant="ghost" 
                  size="sm"
                  onClick={fetchEmails}
                  disabled={isFetchingEmails}
                  className="h-8 w-8 p-0"
                >
                  <ReloadIcon className={`h-4 w-4 ${isFetchingEmails ? 'animate-spin' : ''}`} />
                </Button>
              </div>
              <div className="w-[200px]">
                {isFetchingEmails ? (
                  <div className="p-8 flex justify-center items-center">
                    <ReloadIcon className="h-6 w-6 animate-spin" />
                  </div>
                ) : (
                  <div>
                    {emails.map((email, index) => (
                      <button
                        key={email.id}
                        onClick={() => setSelectedEmail(email)}
                        className={`w-full text-left px-4 py-3 text-sm transition-colors hover:bg-muted
                          ${selectedEmail?.id === email.id ? 'bg-muted' : ''}`}
                      >
                        Random Email #{index + 1}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Email Content */}
            <div className="h-[40vh]">
              {selectedEmail ? (
                <div className="h-full flex flex-col">
                  {/* Email Metadata */}
                  <div className="p-4 border-b space-y-2 flex-shrink-0">
                    <div className="flex items-baseline justify-between">
                      <h2 className="text-xl font-semibold">{selectedEmail.subject}</h2>
                      <span className="text-sm text-muted-foreground">
                        {new Date(selectedEmail.received_at).toLocaleDateString()}
                      </span>
                    </div>
                    <div className="text-sm text-muted-foreground">
                      From: {selectedEmail.sender}
                    </div>
                  </div>
                  {/* Email Body */}
                  <div className="p-4 overflow-auto flex-grow">
                    <div 
                      className="prose max-w-none dark:prose-invert"
                      dangerouslySetInnerHTML={{ __html: selectedEmail.body_html || '' }} 
                    />
                  </div>
                </div>
              ) : (
                <div className="h-full flex items-center justify-center text-muted-foreground p-4">
                  Select an email to view its content
                </div>
              )}
            </div>
          </div>
        </div>

        {/* AI Instructions Container */}
        <div className="space-y-2">
          <h2 className="text-xl font-semibold px-1">AI Agent Instructions</h2>
          <div className="border rounded-lg grid grid-cols-[auto,1fr]">
            {/* Instructions Tabs */}
            <div className="border-r bg-muted/50">
              <div className="sticky top-0 p-2 border-b bg-muted">
                <h2 className="text-sm font-medium">Saved Instructions ({systemInstructions.length}/10)</h2>
              </div>
              <div className="flex flex-col h-[40vh]">
                <div className="flex-1 overflow-auto w-[200px]">
                  {systemInstructions.map((instruction, index) => (
                    <button
                      key={instruction.id}
                      onClick={() => {
                        setSelectedInstruction(instruction.id);
                        setNewInstruction(instruction.text);
                      }}
                      className={`w-full text-left px-4 py-3 text-sm transition-colors hover:bg-muted
                        ${selectedInstruction === instruction.id ? 'bg-muted' : ''}`}
                    >
                      {formatInstructionName(instruction, index)}
                    </button>
                  ))}
                </div>
                <Button
                  onClick={addSystemInstruction}
                  disabled={systemInstructions.length >= 10}
                  variant="ghost"
                  className="w-full rounded-none border-t p-3 h-auto"
                >
                  + Add New
                </Button>
              </div>
            </div>

            {/* Instructions Content */}
            <div className="h-[40vh]">
              <div className="h-full flex flex-col p-4">
                <div className="flex-shrink-0 space-y-4">
                  <div className="text-sm text-muted-foreground">
                    Define how the AI agent should respond to the selected email. Edit the instruction and click Save to update it.
                  </div>
                </div>
                <div className="flex-grow flex flex-col gap-4 mt-4">
                  <Textarea
                    placeholder="Example: Be professional but friendly. Address their questions directly. Sign as 'Property Management Team'."
                    value={newInstruction}
                    onChange={(e) => setNewInstruction(e.target.value)}
                    className="flex-grow min-h-0"
                  />
                  <Button 
                    onClick={updateCurrentInstruction}
                    disabled={!newInstruction.trim()}
                  >
                    Save Changes
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Generate Response Section */}
      <div className="space-y-4">
        <div className="flex justify-between items-baseline">
          <h2 className="text-xl font-semibold">Generated Response</h2>
          <Button
            onClick={generateResponse}
            disabled={!selectedEmail || !selectedInstruction || isLoading}
            className="w-[200px]"
          >
            {isLoading ? (
              <>
                <ReloadIcon className="mr-2 h-4 w-4 animate-spin" />
                Generating...
              </>
            ) : (
              "Generate Response"
            )}
          </Button>
        </div>

        {generatedResponse && (
          <div className="border rounded-lg p-4">
            <Textarea
              value={generatedResponse}
              readOnly
              className="min-h-[200px]"
            />
          </div>
        )}
      </div>

      {/* Footnote */}
      <div className="text-sm text-muted-foreground italic text-center border-t pt-4">
        This frontend UI was created entirely with Cursor AI (Claude 3.5 Sonnet Model)
      </div>
    </div>
  );
} 