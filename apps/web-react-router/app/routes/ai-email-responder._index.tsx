import type { Route } from "./+types/ai-email-responder._index";
import { useState, useEffect } from "react";
import { Link } from "react-router";
import { Button } from "~/components/ui/button";
import { Textarea } from "~/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { RefreshCw, Plus, Save, Trash2, Sparkles } from "lucide-react";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "AI Email Responder | Emilio Esposito" },
    {
      name: "description",
      content: "AI-powered email responder for Zillow rental inquiries",
    },
  ];
}

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

export default function AIEmailResponderPage() {
  const [emails, setEmails] = useState<ZillowEmail[]>([]);
  const [selectedEmail, setSelectedEmail] = useState<ZillowEmail | null>(null);
  const [systemInstructions, setSystemInstructions] = useState<
    SystemInstruction[]
  >(() => {
    // Try to load saved instructions from localStorage
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("systemInstructions");
      if (saved) {
        try {
          return JSON.parse(saved);
        } catch (e) {
          console.error("Failed to parse saved instructions:", e);
        }
      }
    }
    // Default instruction if nothing in localStorage
    return [
      {
        id: "default",
        text: "You are a property manager. Be professional but friendly. Keep the response concise. Address their questions directly. Sign off as 'Property Management Team'.",
      },
    ];
  });

  // Save instructions to localStorage whenever they change
  useEffect(() => {
    localStorage.setItem(
      "systemInstructions",
      JSON.stringify(systemInstructions)
    );
  }, [systemInstructions]);

  const [newInstruction, setNewInstruction] = useState("");
  const [selectedInstruction, setSelectedInstruction] =
    useState<string>("default");
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

    setSystemInstructions((prevInstructions) =>
      prevInstructions.map((instruction) =>
        instruction.id === selectedInstruction
          ? { ...instruction, text: newInstruction.trim() }
          : instruction
      )
    );
  }

  // Helper function to format instruction name
  function formatInstructionName(
    instruction: SystemInstruction,
    index: number
  ): string {
    const previewLength = 20;
    const preview =
      instruction.text.length > previewLength
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

      const response = await fetch(
        "/api/google/gmail/generate_email_response",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            email_content: emailContent,
            system_instruction: selectedInstructionText,
          }),
        }
      );

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

  const currentInstruction = systemInstructions.find(
    (i) => i.id === selectedInstruction,
  );

  function deleteCurrentInstruction() {
    if (!selectedInstruction || systemInstructions.length <= 1) return;
    const remaining = systemInstructions.filter(
      (i) => i.id !== selectedInstruction,
    );
    setSystemInstructions(remaining);
    setSelectedInstruction(remaining[0].id);
    setNewInstruction(remaining[0].text);
  }

  return (
    <div className="container mx-auto py-8 px-4 sm:px-6 max-w-6xl space-y-8">
      {/* Header */}
      <div className="space-y-3">
        <div className="flex items-baseline justify-between">
          <h1 className="text-3xl font-bold">AI Email Responder</h1>
          <Link
            to="/ai-email-responder/architecture"
            className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1.5 transition-colors"
          >
            System Architecture
            <span>→</span>
          </Link>
        </div>
        <p className="text-muted-foreground">
          Test and refine AI agent instructions for automated Zillow rental
          inquiry responses. Select an email, configure the AI instructions, and
          preview the generated response.
        </p>
      </div>

      {/* Step 1: Select Email */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">
            1. Select an Email
          </h2>
          <Button
            variant="outline"
            size="sm"
            onClick={fetchEmails}
            disabled={isFetchingEmails}
          >
            <RefreshCw
              className={`h-4 w-4 mr-2 ${isFetchingEmails ? "animate-spin" : ""}`}
            />
            Refresh
          </Button>
        </div>

        {/* Email list as horizontal cards */}
        {isFetchingEmails ? (
          <div className="flex items-center justify-center py-12 border rounded-lg">
            <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : emails.length === 0 ? (
          <div className="flex items-center justify-center py-12 border rounded-lg text-muted-foreground">
            No emails found. Click Refresh to fetch.
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
            {emails.map((email, index) => (
              <button
                key={email.id}
                onClick={() => setSelectedEmail(email)}
                className={`text-left p-3 rounded-lg border text-sm transition-colors hover:bg-muted ${
                  selectedEmail?.id === email.id
                    ? "border-primary bg-muted ring-1 ring-primary"
                    : ""
                }`}
              >
                <div className="font-medium truncate">
                  Email #{index + 1}
                </div>
                <div className="text-xs text-muted-foreground truncate mt-1">
                  {email.sender}
                </div>
              </button>
            ))}
          </div>
        )}

        {/* Selected email preview */}
        {selectedEmail && (
          <div className="border rounded-lg">
            <div className="p-4 border-b flex items-baseline justify-between gap-4">
              <div className="min-w-0">
                <h3 className="font-semibold truncate">
                  {selectedEmail.subject}
                </h3>
                <p className="text-sm text-muted-foreground mt-0.5">
                  From: {selectedEmail.sender}
                </p>
              </div>
              <span className="text-sm text-muted-foreground shrink-0">
                {new Date(selectedEmail.received_at).toLocaleDateString()}
              </span>
            </div>
            <div className="p-4 max-h-[300px] overflow-auto">
              <div
                className="prose max-w-none dark:prose-invert text-sm"
                dangerouslySetInnerHTML={{
                  __html: selectedEmail.body_html || "",
                }}
              />
            </div>
          </div>
        )}
      </section>

      {/* Step 2: AI Instructions */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">
          2. Configure AI Instructions
        </h2>
        <div className="border rounded-lg p-4 space-y-4">
          <div className="flex items-center gap-3">
            <Select
              value={selectedInstruction}
              onValueChange={(value) => {
                setSelectedInstruction(value);
                const instruction = systemInstructions.find(
                  (i) => i.id === value,
                );
                if (instruction) setNewInstruction(instruction.text);
              }}
            >
              <SelectTrigger className="flex-1">
                <SelectValue placeholder="Select an instruction set" />
              </SelectTrigger>
              <SelectContent>
                {systemInstructions.map((instruction, index) => (
                  <SelectItem key={instruction.id} value={instruction.id}>
                    {formatInstructionName(instruction, index)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="sm"
              onClick={addSystemInstruction}
              disabled={
                systemInstructions.length >= 10 || !newInstruction.trim()
              }
            >
              <Plus className="h-4 w-4 mr-1.5" />
              Add New
            </Button>
            {systemInstructions.length > 1 && (
              <Button
                variant="outline"
                size="sm"
                onClick={deleteCurrentInstruction}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            )}
          </div>

          <Textarea
            placeholder="Example: Be professional but friendly. Address their questions directly. Sign as 'Property Management Team'."
            value={newInstruction}
            onChange={(e) => setNewInstruction(e.target.value)}
            rows={5}
          />

          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">
              {systemInstructions.length}/10 instruction sets saved
            </p>
            <Button
              size="sm"
              variant="outline"
              onClick={updateCurrentInstruction}
              disabled={
                !newInstruction.trim() ||
                newInstruction === currentInstruction?.text
              }
            >
              <Save className="h-4 w-4 mr-1.5" />
              Save Changes
            </Button>
          </div>
        </div>
      </section>

      {/* Step 3: Generate */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">
            3. Generate Response
          </h2>
          <Button
            onClick={generateResponse}
            disabled={!selectedEmail || !selectedInstruction || isLoading}
          >
            {isLoading ? (
              <>
                <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
                Generating...
              </>
            ) : (
              <>
                <Sparkles className="h-4 w-4 mr-2" />
                Generate
              </>
            )}
          </Button>
        </div>

        {generatedResponse ? (
          <div className="border rounded-lg p-4">
            <Textarea
              value={generatedResponse}
              readOnly
              className="min-h-[200px]"
            />
          </div>
        ) : (
          <div className="flex items-center justify-center py-12 border rounded-lg text-muted-foreground text-sm">
            {!selectedEmail
              ? "Select an email above to get started"
              : "Click Generate to preview the AI response"}
          </div>
        )}
      </section>

      {/* Footer links */}
      <div className="text-sm text-muted-foreground text-center border-t pt-4">
        In production, responses are generated automatically via{" "}
        <Link
          to="/api/docs#/google/handle_gmail_notifications_api_google_pubsub_gmail_notifications_post"
          className="text-foreground underline underline-offset-4 hover:text-foreground/80 transition-colors"
        >
          Google PubSub webhook
        </Link>
        .{" "}
        <Link
          to="/ai-email-responder/architecture"
          className="text-foreground underline underline-offset-4 hover:text-foreground/80 transition-colors"
        >
          View architecture
        </Link>
      </div>
    </div>
  );
}
