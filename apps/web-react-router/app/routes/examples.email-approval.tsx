import { useState, useEffect, useCallback } from "react";
import type { Route } from "./+types/examples.email-approval";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import { Input } from "~/components/ui/input";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Email Approval Demo | PydanticAI Human-in-the-Loop" },
    {
      name: "description",
      content:
        "Demo of PydanticAI agent with human-in-the-loop email approval",
    },
  ];
}

type WorkflowStatus =
  | "pending"
  | "awaiting_approval"
  | "approved"
  | "denied"
  | "completed"
  | "failed";

interface EmailDetails {
  to: string;
  subject: string;
  body: string;
}

interface Workflow {
  workflow_id: string;
  status: WorkflowStatus;
  user_message: string;
  email_details: EmailDetails | null;
  tool_call_id: string | null;
  agent_response: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

const statusColors: Record<WorkflowStatus, string> = {
  pending: "bg-yellow-100 text-yellow-800",
  awaiting_approval: "bg-blue-100 text-blue-800",
  approved: "bg-green-100 text-green-800",
  denied: "bg-red-100 text-red-800",
  completed: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

const statusLabels: Record<WorkflowStatus, string> = {
  pending: "Pending",
  awaiting_approval: "Awaiting Approval",
  approved: "Approved",
  denied: "Denied",
  completed: "Completed",
  failed: "Failed",
};

export default function EmailApprovalDemo() {
  const [userMessage, setUserMessage] = useState(
    "Please send an email to test@example.com with subject 'Hello' and body 'This is a test email.'"
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [processingApproval, setProcessingApproval] = useState<string | null>(
    null
  );

  const fetchWorkflows = useCallback(async () => {
    try {
      const response = await fetch("/api/ai/email-approval/workflows");
      if (response.ok) {
        const data = await response.json();
        setWorkflows(data);
      }
    } catch (err) {
      console.error("Failed to fetch workflows:", err);
    }
  }, []);

  useEffect(() => {
    fetchWorkflows();
    // Poll for updates every 3 seconds
    const interval = setInterval(fetchWorkflows, 3000);
    return () => clearInterval(interval);
  }, [fetchWorkflows]);

  const startWorkflow = async () => {
    if (!userMessage.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const response = await fetch("/api/ai/email-approval/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_message: userMessage }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Failed to start workflow");
      }

      // Refresh workflow list
      await fetchWorkflows();
      setUserMessage("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const handleApproval = async (workflowId: string, approved: boolean) => {
    setProcessingApproval(workflowId);

    try {
      const response = await fetch(
        `/api/ai/email-approval/approve/${workflowId}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            approved,
            reason: approved ? undefined : "User denied the email",
          }),
        }
      );

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Failed to process approval");
      }

      // Refresh workflow list
      await fetchWorkflows();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setProcessingApproval(null);
    }
  };

  return (
    <div className="container mx-auto py-10 px-4 md:px-8 max-w-4xl">
      <h1 className="text-4xl font-bold mb-4">
        Email Approval Demo
      </h1>
      <p className="text-muted-foreground mb-8">
        PydanticAI agent with human-in-the-loop email approval using deferred
        tools
      </p>

      {/* Start Workflow Card */}
      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Start New Workflow</CardTitle>
          <CardDescription>
            Ask the agent to send an email. It will pause and wait for your
            approval before actually sending.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Input
            value={userMessage}
            onChange={(e) => setUserMessage(e.target.value)}
            placeholder="Ask the agent to send an email..."
            disabled={loading}
          />
          <div className="flex gap-2">
            <Button onClick={startWorkflow} disabled={loading || !userMessage.trim()}>
              {loading ? "Starting..." : "Start Workflow"}
            </Button>
            <Button variant="outline" onClick={fetchWorkflows}>
              Refresh
            </Button>
          </div>
          {error && (
            <Alert variant="destructive">
              <AlertTitle>Error</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

      {/* Workflows List */}
      <Card>
        <CardHeader>
          <CardTitle>Workflows</CardTitle>
          <CardDescription>
            {workflows.length === 0
              ? "No workflows yet. Start one above!"
              : `${workflows.length} workflow(s)`}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {workflows.map((workflow) => (
            <div
              key={workflow.workflow_id}
              className="border rounded-lg p-4 space-y-3"
            >
              {/* Header with ID and Status */}
              <div className="flex items-center justify-between">
                <code className="text-xs text-muted-foreground">
                  {workflow.workflow_id.slice(0, 8)}...
                </code>
                <span
                  className={`px-2 py-1 rounded text-xs font-medium ${
                    statusColors[workflow.status]
                  }`}
                >
                  {statusLabels[workflow.status]}
                </span>
              </div>

              {/* User Message */}
              <div>
                <p className="text-sm font-medium">User Request:</p>
                <p className="text-sm text-muted-foreground">
                  {workflow.user_message}
                </p>
              </div>

              {/* Email Details (if awaiting approval) */}
              {workflow.status === "awaiting_approval" &&
                workflow.email_details && (
                  <div className="bg-muted/50 rounded-lg p-3 space-y-2">
                    <p className="text-sm font-medium">
                      Email Pending Approval:
                    </p>
                    <div className="text-sm space-y-1">
                      <p>
                        <strong>To:</strong> {workflow.email_details.to}
                      </p>
                      <p>
                        <strong>Subject:</strong> {workflow.email_details.subject}
                      </p>
                      <p>
                        <strong>Body:</strong> {workflow.email_details.body}
                      </p>
                    </div>
                    <div className="flex gap-2 mt-3">
                      <Button
                        size="sm"
                        onClick={() =>
                          handleApproval(workflow.workflow_id, true)
                        }
                        disabled={processingApproval === workflow.workflow_id}
                      >
                        {processingApproval === workflow.workflow_id
                          ? "Processing..."
                          : "Approve"}
                      </Button>
                      <Button
                        size="sm"
                        variant="destructive"
                        onClick={() =>
                          handleApproval(workflow.workflow_id, false)
                        }
                        disabled={processingApproval === workflow.workflow_id}
                      >
                        Deny
                      </Button>
                    </div>
                  </div>
                )}

              {/* Agent Response (if completed) */}
              {workflow.agent_response && (
                <div>
                  <p className="text-sm font-medium">Agent Response:</p>
                  <p className="text-sm text-muted-foreground">
                    {workflow.agent_response}
                  </p>
                </div>
              )}

              {/* Error (if failed) */}
              {workflow.error && (
                <Alert variant="destructive">
                  <AlertTitle>Error</AlertTitle>
                  <AlertDescription>{workflow.error}</AlertDescription>
                </Alert>
              )}

              {/* Timestamp */}
              <p className="text-xs text-muted-foreground">
                Created: {new Date(workflow.created_at).toLocaleString()}
              </p>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* How it Works */}
      <Card className="mt-8">
        <CardHeader>
          <CardTitle>How It Works</CardTitle>
        </CardHeader>
        <CardContent className="prose prose-sm dark:prose-invert">
          <ol className="list-decimal list-inside space-y-2 text-sm">
            <li>
              <strong>Start Workflow:</strong> You send a message asking the
              agent to send an email
            </li>
            <li>
              <strong>Agent Processes:</strong> The PydanticAI agent parses your
              request and calls the <code>send_email</code> tool
            </li>
            <li>
              <strong>Deferred Tool:</strong> Since <code>send_email</code> has{" "}
              <code>requires_approval=True</code>, the agent pauses and returns
              a <code>DeferredToolRequests</code>
            </li>
            <li>
              <strong>Human Review:</strong> You see the email details and can
              approve or deny
            </li>
            <li>
              <strong>Resume:</strong> The workflow resumes with your decision
              using <code>DeferredToolResults</code>
            </li>
            <li>
              <strong>Complete:</strong> If approved, the email is "sent" and
              the agent responds
            </li>
          </ol>
          <p className="text-sm text-muted-foreground mt-4">
            This pattern is useful for any tool that requires human oversight
            before execution (financial transactions, data deletion, etc.)
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
