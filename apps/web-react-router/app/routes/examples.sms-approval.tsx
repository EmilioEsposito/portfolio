import { useState, useEffect, useCallback } from "react";
import type { Route } from "./+types/examples.sms-approval";
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
    { title: "SMS Approval Demo | PydanticAI + DBOS" },
    {
      name: "description",
      content: "Human-in-the-loop SMS approval with PydanticAI and DBOS",
    },
  ];
}

interface SMS {
  to: string;
  body: string;
}

interface Workflow {
  workflow_id: string;
  status: string;
  sms?: SMS;
  response?: string;
}

export default function SMSApprovalDemo() {
  const [userMessage, setUserMessage] = useState(
    "Send a friendly hello text to Emilio"
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [processingId, setProcessingId] = useState<string | null>(null);

  const fetchWorkflows = useCallback(async () => {
    try {
      const res = await fetch("/api/ai/sms-approval/workflows");
      if (res.ok) setWorkflows(await res.json());
    } catch (err) {
      console.error("Failed to fetch workflows:", err);
    }
  }, []);

  useEffect(() => {
    fetchWorkflows();
    const interval = setInterval(fetchWorkflows, 3000);
    return () => clearInterval(interval);
  }, [fetchWorkflows]);

  const startWorkflow = async () => {
    if (!userMessage.trim()) return;
    setLoading(true);
    setError(null);

    try {
      const res = await fetch("/api/ai/sms-approval/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_message: userMessage }),
      });

      if (!res.ok) throw new Error((await res.json()).detail || "Failed");
      await fetchWorkflows();
      setUserMessage("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const handleApproval = async (workflowId: string, approved: boolean) => {
    setProcessingId(workflowId);
    try {
      const res = await fetch(`/api/ai/sms-approval/approve/${workflowId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved, reason: approved ? undefined : "Denied by user" }),
      });

      if (!res.ok) throw new Error((await res.json()).detail || "Failed");
      await fetchWorkflows();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setProcessingId(null);
    }
  };

  return (
    <div className="container mx-auto py-10 px-4 md:px-8 max-w-4xl">
      <h1 className="text-4xl font-bold mb-4">SMS Approval Demo</h1>
      <p className="text-muted-foreground mb-8">
        PydanticAI + DBOS: Human-in-the-loop with durable execution
      </p>

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Start Workflow</CardTitle>
          <CardDescription>
            Ask the agent to send an SMS. It will pause for your approval before sending.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Input
            value={userMessage}
            onChange={(e) => setUserMessage(e.target.value)}
            placeholder="Ask the agent to send a text message..."
            disabled={loading}
          />
          <div className="flex gap-2">
            <Button onClick={startWorkflow} disabled={loading || !userMessage.trim()}>
              {loading ? "Starting..." : "Start"}
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

      <Card>
        <CardHeader>
          <CardTitle>Pending Approvals</CardTitle>
          <CardDescription>
            {workflows.length === 0 ? "No pending workflows" : `${workflows.length} pending`}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {workflows.map((w) => (
            <div key={w.workflow_id} className="border rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between">
                <code className="text-xs text-muted-foreground">
                  {w.workflow_id.slice(0, 8)}...
                </code>
                <span className="px-2 py-1 rounded text-xs font-medium bg-blue-100 text-blue-800">
                  {w.status}
                </span>
              </div>

              {w.sms && (
                <div className="bg-muted/50 rounded-lg p-3 space-y-2">
                  <p className="text-sm font-medium">SMS to approve:</p>
                  <div className="text-sm space-y-1">
                    <p><strong>To:</strong> {w.sms.to}</p>
                    <p><strong>Message:</strong> {w.sms.body}</p>
                  </div>
                  <div className="flex gap-2 mt-3">
                    <Button
                      size="sm"
                      onClick={() => handleApproval(w.workflow_id, true)}
                      disabled={processingId === w.workflow_id}
                    >
                      {processingId === w.workflow_id ? "..." : "Approve"}
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => handleApproval(w.workflow_id, false)}
                      disabled={processingId === w.workflow_id}
                    >
                      Deny
                    </Button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="mt-8">
        <CardHeader>
          <CardTitle>How It Works</CardTitle>
        </CardHeader>
        <CardContent className="prose prose-sm dark:prose-invert">
          <ol className="list-decimal list-inside space-y-2 text-sm">
            <li><strong>DBOS Workflow:</strong> Agent runs inside a durable workflow</li>
            <li><strong>Deferred Tool:</strong> <code>send_sms</code> has <code>requires_approval=True</code></li>
            <li><strong>DBOS recv/send:</strong> Workflow waits via <code>DBOS.recv()</code> until approval</li>
            <li><strong>Approval:</strong> Your decision triggers <code>DBOS.send()</code> to resume</li>
          </ol>
          <p className="text-sm text-muted-foreground mt-4">
            If the server crashes, DBOS can recover workflows from the database.
            SMS is sent via OpenPhone API when approved.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
