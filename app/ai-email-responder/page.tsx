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
  const [systemInstructions, setSystemInstructions] = useState<SystemInstruction[]>([
    {
      id: "default",
      text: "You are a property manager. Be professional but friendly. Keep the response concise. Address their questions directly. Sign off as 'Property Management Team'."
    }
  ]);
  const [newInstruction, setNewInstruction] = useState("");
  const [selectedInstruction, setSelectedInstruction] = useState<string | null>(null);
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
      const response = await fetch("/api/google/get_zillow_emails");
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
    setNewInstruction("");
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

      const response = await fetch("/api/google/generate_email_response", {
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
    <div className="container mx-auto py-8 space-y-8">
      <h1 className="text-3xl font-bold mb-6">AI Email Responder</h1>
      
      {/* Sample Emails and Preview Section */}
      <div className="space-y-4 lg:space-y-0 lg:grid lg:grid-cols-[1fr,2fr] lg:gap-6">
        {/* Email Selection */}
        <div className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-2xl font-semibold">Select a Sample Email</h2>
            <Button 
              variant="outline" 
              size="sm"
              onClick={fetchEmails}
              disabled={isFetchingEmails}
            >
              {isFetchingEmails ? (
                <>
                  <ReloadIcon className="mr-2 h-4 w-4 animate-spin" />
                  Loading
                </>
              ) : (
                <>
                  <ReloadIcon className="mr-2 h-4 w-4" />
                  Refresh
                </>
              )}
            </Button>
          </div>
          <div className="border rounded-lg">
            {isFetchingEmails ? (
              <div className="p-8 flex justify-center items-center">
                <ReloadIcon className="h-6 w-6 animate-spin" />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Subject</TableHead>
                    <TableHead className="w-[100px]">Received</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {emails.map((email) => (
                    <TableRow 
                      key={email.id}
                      className={`cursor-pointer ${selectedEmail?.id === email.id ? 'bg-muted' : ''}`}
                      onClick={() => setSelectedEmail(email)}
                    >
                      <TableCell className="max-w-[200px] truncate">
                        {email.subject}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        {new Date(email.received_at).toLocaleDateString()}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        </div>

        {/* Selected Email Preview */}
        <div className="space-y-4">
          <h2 className="text-2xl font-semibold">Email Content</h2>
          <div className="border rounded-lg p-4 h-[50vh] overflow-auto">
            {selectedEmail ? (
              <div 
                className="prose max-w-none dark:prose-invert"
                dangerouslySetInnerHTML={{ __html: selectedEmail.body_html || '' }} 
              />
            ) : (
              <div className="h-full flex items-center justify-center text-muted-foreground">
                Select an email to view its content
              </div>
            )}
          </div>
        </div>
      </div>

      {/* System Instructions Section */}
      <div className="space-y-4">
        <h2 className="text-2xl font-semibold">Response Style</h2>
        <div className="grid grid-cols-[2fr,1fr] gap-6">
          {/* Input Area */}
          <div className="space-y-4">
            <div className="text-sm text-muted-foreground">
              Define how the AI should respond to the selected email. Click a saved style to edit it.
            </div>
            <div className="flex gap-4">
              <Textarea
                placeholder="Example: Be professional but friendly. Address their questions directly. Sign as 'Property Management Team'."
                value={newInstruction}
                onChange={(e) => setNewInstruction(e.target.value)}
                className="flex-1 min-h-[100px]"
              />
              <Button 
                onClick={addSystemInstruction}
                disabled={!newInstruction.trim() || systemInstructions.length >= 10}
                className="self-start"
              >
                Save Style
              </Button>
            </div>
          </div>

          {/* Saved Styles Table */}
          <div className="space-y-2">
            <div className="text-sm text-muted-foreground">Saved Styles ({systemInstructions.length}/10)</div>
            <div className="border rounded-lg">
              <Table>
                <TableBody>
                  {systemInstructions.map((instruction) => (
                    <TableRow 
                      key={instruction.id}
                      className={`cursor-pointer ${selectedInstruction === instruction.id ? 'bg-muted' : ''}`}
                      onClick={() => {
                        setSelectedInstruction(instruction.id);
                        setNewInstruction(instruction.text);
                      }}
                    >
                      <TableCell className="max-w-[300px] truncate">
                        {instruction.text}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        </div>
      </div>

      {/* Generate Response Section */}
      <div className="space-y-4">
        <Button
          onClick={generateResponse}
          disabled={!selectedEmail || !selectedInstruction || isLoading}
          className="w-full"
        >
          {isLoading ? (
            <>
              <ReloadIcon className="mr-2 h-4 w-4 animate-spin" />
              Generating Response...
            </>
          ) : (
            "Generate Response"
          )}
        </Button>

        {generatedResponse && (
          <div className="border rounded-lg p-4">
            <h3 className="font-semibold mb-2">Generated Response</h3>
            <Textarea
              value={generatedResponse}
              readOnly
              className="min-h-[200px]"
            />
          </div>
        )}
      </div>
    </div>
  );
} 