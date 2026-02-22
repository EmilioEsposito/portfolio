import { useState, useEffect, useCallback } from "react";
import type { Route } from "./+types/hitl-agent-workflow";
import { useAuth } from "@clerk/react-router";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import { Input } from "~/components/ui/input";
import { Textarea } from "~/components/ui/textarea";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Label } from "~/components/ui/label";
import { AuthGuard } from "~/components/auth-guard";
import {
  RefreshCw,
  Play,
  CheckCircle2,
  XCircle,
  Clock,
  AlertCircle,
  MessageSquare,
  Loader2,
  Edit3,
} from "lucide-react";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "HITL Agent Workflows | Approval Queue" },
    {
      name: "description",
      content: "Review and approve pending HITL agent workflows",
    },
  ];
}

interface PendingApproval {
  tool_call_id: string;
  tool_name: string;
  args: {
    to?: string;
    body?: string;
    [key: string]: any;
  };
}

interface Conversation {
  conversation_id: string;
  agent_name: string;
  clerk_user_id: string | null;
  pending: PendingApproval[];  // Always a list (empty if none)
  created_at: string | null;
  updated_at: string | null;
}

export default function HITLAgentWorkflowPage() {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const [prompt, setPrompt] = useState("Send a creative message to Emilio");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [editedBodies, setEditedBodies] = useState<Record<string, string>>({});
  const [refreshing, setRefreshing] = useState(false);

  const fetchPendingConversations = useCallback(async () => {
    if (!isSignedIn) return;
    setRefreshing(true);
    try {
      const token = await getToken();
      const res = await fetch("/api/ai-demos/hitl-agent/workflow/pending", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        console.error("Failed to fetch conversations");
        return;
      }

      const data = await res.json();
      setConversations(data.conversations || []);
    } catch (err) {
      console.error("Failed to fetch conversations:", err);
    } finally {
      setRefreshing(false);
    }
  }, [isSignedIn, getToken]);

  useEffect(() => {
    if (!isSignedIn) return;
    fetchPendingConversations();
    // Poll every 10 seconds
    const interval = setInterval(fetchPendingConversations, 10000);
    return () => clearInterval(interval);
  }, [fetchPendingConversations, isSignedIn]);

  const startConversation = async () => {
    if (!prompt.trim() || !isSignedIn) return;
    setLoading(true);
    setError(null);

    try {
      const token = await getToken();
      const res = await fetch("/api/ai-demos/hitl-agent/workflow/start", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ prompt }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Failed to start conversation");
      }

      const result = await res.json();

      // If there are pending approvals, add to the list
      if (result.pending && result.pending.length > 0) {
        setConversations((prev) => [
          {
            conversation_id: result.conversation_id,
            agent_name: "hitl_sms_agent",
            clerk_user_id: null,
            pending: result.pending,  // Already a list from backend
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
          ...prev,
        ]);
      }

      setPrompt("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const handleApproval = async (conversationId: string, approved: boolean) => {
    const conv = conversations.find((c) => c.conversation_id === conversationId);
    if (!conv?.pending || conv.pending.length === 0 || !isSignedIn) return;

    setProcessingId(conversationId);
    setError(null);

    try {
      const token = await getToken();

      // Build batch decisions for all pending approvals
      const decisions = conv.pending.map((p) => {
        const editKey = `${conversationId}:${p.tool_call_id}`;
        const overrideBody = editedBodies[editKey];
        const overrideArgs =
          overrideBody && overrideBody !== p.args.body
            ? { body: overrideBody }
            : undefined;

        return {
          tool_call_id: p.tool_call_id,
          approved,
          override_args: overrideArgs,
          reason: approved ? undefined : "Denied by user",
        };
      });

      const res = await fetch(`/api/ai-demos/hitl-agent/conversation/${conversationId}/approve`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ decisions }),
      });

      if (!res.ok) {
        const data = await res.json();
        const detail = data.detail;
        const message = typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((d: any) => d.msg || JSON.stringify(d)).join(", ")
            : "Failed to process approval";
        throw new Error(message);
      }

      // Clear edited bodies for this conversation
      setEditedBodies((prev) => {
        const next = { ...prev };
        conv.pending.forEach((p) => {
          delete next[`${conversationId}:${p.tool_call_id}`];
        });
        return next;
      });

      // Remove the approved conversation from the list
      setConversations((prev) =>
        prev.filter((c) => c.conversation_id !== conversationId)
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setProcessingId(null);
    }
  };

  const formatDate = (dateString: string | null) => {
    if (!dateString) return "Unknown";
    const date = new Date(dateString);
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const pendingCount = conversations.length;

  return (
    <AuthGuard
      message="Sign in to review and approve pending agent actions"
      icon={<MessageSquare className="w-16 h-16 text-muted-foreground" />}
    >
    <div className="container mx-auto py-10 px-4 md:px-8 max-w-4xl">
      <div className="flex items-center gap-3 mb-2">
        <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center">
          <MessageSquare className="w-5 h-5 text-primary" />
        </div>
        <div>
          <h1 className="text-3xl font-bold">HITL Agent Workflows</h1>
          <p className="text-muted-foreground">
            Review and approve pending agent actions
          </p>
        </div>
      </div>

      <div className="flex justify-end mb-6">
        <Button
          variant="outline"
          onClick={fetchPendingConversations}
          disabled={refreshing}
          className="gap-2"
        >
          <RefreshCw className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Start New Conversation */}
      <Card className="mb-8">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Play className="w-5 h-5" />
            Start New Conversation
          </CardTitle>
          <CardDescription>
            Ask the agent to do something. If it needs approval (e.g., sending an SMS),
            it will appear below for your review.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex gap-2">
            <Input
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Ask the agent to do something..."
              disabled={loading}
              className="flex-1"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  startConversation();
                }
              }}
            />
            <Button onClick={startConversation} disabled={loading || !prompt.trim()}>
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Starting...
                </>
              ) : (
                "Start"
              )}
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

      {/* Pending Approvals */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Clock className="w-5 h-5 text-amber-500" />
                Pending Approvals
              </CardTitle>
              <CardDescription>
                {pendingCount === 0
                  ? "No conversations awaiting approval"
                  : `${pendingCount} conversation${pendingCount > 1 ? "s" : ""} awaiting your decision`}
              </CardDescription>
            </div>
            {pendingCount > 0 && (
              <Badge variant="secondary" className="text-lg px-3 py-1">
                {pendingCount}
              </Badge>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {conversations.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <Clock className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p>No pending approvals</p>
              <p className="text-sm mt-1">Start a new conversation above to get started</p>
            </div>
          ) : (
            conversations.map((conv) => (
              <div
                key={conv.conversation_id}
                className="border rounded-lg p-4 space-y-3 hover:bg-muted/30 transition-colors"
              >
                {/* Header */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <AlertCircle className="w-4 h-4 text-amber-500" />
                    <code className="text-xs text-muted-foreground font-mono">
                      {conv.conversation_id.slice(0, 8)}...
                    </code>
                    {conv.created_at && (
                      <span className="text-xs text-muted-foreground">
                        {formatDate(conv.created_at)}
                      </span>
                    )}
                  </div>
                  <Badge variant="outline">Pending</Badge>
                </div>

                {/* Pending Approval Details */}
                {conv.pending && conv.pending.length > 0 && (
                  <div className="bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-800 rounded-lg p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">
                        {conv.pending.length === 1 ? "Approval Required" : `${conv.pending.length} Approvals Required`}
                      </span>
                    </div>

                    {/* Render each pending approval */}
                    {conv.pending.map((p) => {
                      const editKey = `${conv.conversation_id}:${p.tool_call_id}`;
                      return (
                        <div key={p.tool_call_id} className="space-y-2 border-t pt-3 first:border-t-0 first:pt-0">
                          <code className="text-xs bg-muted px-2 py-0.5 rounded">
                            {p.tool_name}
                          </code>

                          {/* Recipient */}
                          <div className="text-sm">
                            <Label className="text-muted-foreground text-xs">To</Label>
                            <p className="font-mono">
                              {p.args.to || "Default (Emilio)"}
                            </p>
                          </div>

                          {/* Message - editable */}
                          <div className="text-sm">
                            <div className="flex items-center justify-between mb-1">
                              <Label className="text-muted-foreground text-xs">Message</Label>
                              {!editedBodies[editKey] && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-6 px-2 text-xs"
                                  onClick={() =>
                                    setEditedBodies((prev) => ({
                                      ...prev,
                                      [editKey]: p.args.body || "",
                                    }))
                                  }
                                  disabled={processingId === conv.conversation_id}
                                >
                                  <Edit3 className="w-3 h-3 mr-1" />
                                  Edit
                                </Button>
                              )}
                            </div>
                            {editedBodies[editKey] !== undefined ? (
                              <div className="space-y-2">
                                <Textarea
                                  value={editedBodies[editKey]}
                                  onChange={(e) =>
                                    setEditedBodies((prev) => ({
                                      ...prev,
                                      [editKey]: e.target.value,
                                    }))
                                  }
                                  className="min-h-[80px] bg-background"
                                  disabled={processingId === conv.conversation_id}
                                />
                                {editedBodies[editKey] !== p.args.body && (
                                  <p className="text-xs text-amber-600 dark:text-amber-400">
                                    Modified - will override AI's suggestion
                                  </p>
                                )}
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() =>
                                    setEditedBodies((prev) => {
                                      const next = { ...prev };
                                      delete next[editKey];
                                      return next;
                                    })
                                  }
                                  disabled={processingId === conv.conversation_id}
                                >
                                  Cancel edit
                                </Button>
                              </div>
                            ) : (
                              <p className="p-2 bg-background rounded border whitespace-pre-wrap">
                                {p.args.body}
                              </p>
                            )}
                          </div>
                        </div>
                      );
                    })}

                    <div className="flex gap-2 pt-2">
                      <Button
                        size="sm"
                        onClick={() => handleApproval(conv.conversation_id, true)}
                        disabled={processingId === conv.conversation_id}
                        className="gap-1"
                      >
                        {processingId === conv.conversation_id ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <CheckCircle2 className="w-4 h-4" />
                        )}
                        {processingId === conv.conversation_id
                          ? "Processing..."
                          : conv.pending.length === 1 ? "Approve & Send" : "Approve All"}
                      </Button>
                      <Button
                        size="sm"
                        variant="destructive"
                        onClick={() => handleApproval(conv.conversation_id, false)}
                        disabled={processingId === conv.conversation_id}
                        className="gap-1"
                      >
                        <XCircle className="w-4 h-4" />
                        Deny
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            ))
          )}
        </CardContent>
      </Card>

      {/* How It Works */}
      <Card className="mt-8">
        <CardHeader>
          <CardTitle>How It Works</CardTitle>
        </CardHeader>
        <CardContent className="prose prose-sm dark:prose-invert max-w-none">
          <ol className="list-decimal list-inside space-y-2 text-sm">
            <li>
              <strong>Start Conversation:</strong> Your prompt runs the agent
            </li>
            <li>
              <strong>Agent Proposes Action:</strong> If the agent needs to send an SMS,
              it returns a <code className="bg-muted px-1 rounded">DeferredToolRequests</code>
            </li>
            <li>
              <strong>Conversation Saved:</strong> The conversation state is saved to the database
            </li>
            <li>
              <strong>Your Review:</strong> The pending action appears here for your approval
            </li>
            <li>
              <strong>Resume Agent:</strong> Your decision triggers a second agent run with{" "}
              <code className="bg-muted px-1 rounded">DeferredToolResults</code>
            </li>
          </ol>
          <p className="text-sm text-muted-foreground mt-4">
            <strong>Simple Pattern:</strong> This uses PydanticAI's dual-run pattern - no
            complex workflow orchestration needed. The conversation can wait days or weeks
            for approval since all state is in the database.
          </p>
        </CardContent>
      </Card>
    </div>
    </AuthGuard>
  );
}
