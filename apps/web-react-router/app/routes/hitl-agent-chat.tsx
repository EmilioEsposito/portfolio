import type { Route } from "./+types/hitl-agent-chat";
import { useState, useRef, useEffect, useCallback } from "react";
import { useSearchParams, useNavigate } from "react-router";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useAuth } from "@clerk/react-router";
import { Button } from "~/components/ui/button";
import { Textarea } from "~/components/ui/textarea";
import { useScrollToBottom } from "~/hooks/use-scroll-to-bottom";
import { cn } from "~/lib/utils";
import { Markdown } from "~/components/markdown";
import { AuthGuard } from "~/components/auth-guard";
import {
  MessageSquare,
  Zap,
  StopCircle,
  Send,
  AlertCircle,
  CheckCircle2,
  XCircle,
  Loader2,
  Edit3,
  History,
  Plus,
  Clock,
  Trash2,
} from "lucide-react";
import { Badge } from "~/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import { Label } from "~/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "~/components/ui/sheet";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "HITL Agent Chat | Human-in-the-Loop Demo" },
    {
      name: "description",
      content: "Chat with an AI agent that requires approval for sensitive actions",
    },
  ];
}

const suggestedPrompts = [
  { title: "Send a friendly hello", prompt: "Send a friendly hello message to Emilio" },
  { title: "Send a haiku", prompt: "Send a creative haiku about winter to Emilio" },
  { title: "Send a reminder", prompt: "Send a reminder to Emilio about the meeting tomorrow" },
];

// Convert pending from API format (snake_case array) to frontend format (camelCase single)
// API returns: [{tool_call_id, tool_name, args}, ...] or empty array/null
// Frontend expects: {toolCallId, toolName, args} or null
function convertPendingFromApi(pending: any[] | null): PendingApproval | null {
  if (!pending || pending.length === 0) return null;
  const first = pending[0];
  return {
    toolCallId: first.tool_call_id,
    toolName: first.tool_name,
    args: first.args || {},
  };
}

interface PendingApproval {
  toolCallId: string;
  toolName: string;
  args: Record<string, any>;
}

// Tool approval card with edit capability
function ToolApprovalCard({
  pending,
  conversationId,
  onApprovalComplete,
  isProcessing,
  getToken,
}: {
  pending: PendingApproval;
  conversationId: string;
  onApprovalComplete: (result: any) => void;
  isProcessing: boolean;
  getToken: () => Promise<string | null>;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [editedBody, setEditedBody] = useState(pending.args?.body || "");
  const [processing, setProcessing] = useState(false);

  const handleApproval = async (approved: boolean) => {
    setProcessing(true);
    try {
      const token = await getToken();
      const overrideArgs = isEditing && editedBody !== pending.args?.body
        ? { body: editedBody }
        : undefined;

      const url = `/api/ai-demos/hitl-agent/conversation/${conversationId}/approve`;
      console.log("Approval URL:", url, "conversationId:", conversationId);

      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          decisions: [{
            tool_call_id: pending.toolCallId,
            approved,
            override_args: overrideArgs,
            reason: approved ? undefined : "Denied by user",
          }],
        }),
      });

      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || "Failed to process approval");
      }

      const result = await res.json();
      onApprovalComplete(result);
    } catch (err) {
      console.error("Approval error:", err);
      alert(err instanceof Error ? err.message : "Failed to process approval");
    } finally {
      setProcessing(false);
    }
  };

  const isDisabled = processing || isProcessing;

  return (
    <Card className="border-2 border-amber-500 bg-amber-50 dark:bg-amber-950/20">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-5 h-5 text-amber-500" />
            <CardTitle className="text-sm font-medium">Approval Required</CardTitle>
          </div>
          <Badge variant="outline">{pending.toolName}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Recipient */}
        {pending.args?.to && (
          <div className="text-sm">
            <Label className="text-muted-foreground text-xs">To</Label>
            <p className="font-mono">{pending.args.to}</p>
          </div>
        )}
        {!pending.args?.to && (
          <div className="text-sm">
            <Label className="text-muted-foreground text-xs">To</Label>
            <p className="font-mono text-muted-foreground">Default (Emilio)</p>
          </div>
        )}

        {/* Message - editable */}
        <div className="text-sm">
          <div className="flex items-center justify-between mb-1">
            <Label className="text-muted-foreground text-xs">Message</Label>
            {!isEditing && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-xs"
                onClick={() => setIsEditing(true)}
                disabled={isDisabled}
              >
                <Edit3 className="w-3 h-3 mr-1" />
                Edit
              </Button>
            )}
          </div>
          {isEditing ? (
            <div className="space-y-2">
              <Textarea
                value={editedBody}
                onChange={(e) => setEditedBody(e.target.value)}
                className="min-h-[80px] bg-background"
                disabled={isDisabled}
              />
              {editedBody !== pending.args?.body && (
                <p className="text-xs text-amber-600 dark:text-amber-400">
                  Modified - will override AI's suggestion
                </p>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setIsEditing(false);
                  setEditedBody(pending.args?.body || "");
                }}
                disabled={isDisabled}
              >
                Cancel edit
              </Button>
            </div>
          ) : (
            <p className="p-2 bg-background rounded border whitespace-pre-wrap">
              {pending.args?.body || "(No message body)"}
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-2 pt-2">
          <Button
            size="sm"
            onClick={() => handleApproval(true)}
            disabled={isDisabled}
            className="gap-1"
          >
            {processing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <CheckCircle2 className="w-4 h-4" />
            )}
            {processing ? "Sending..." : "Approve & Send"}
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={() => handleApproval(false)}
            disabled={isDisabled}
            className="gap-1"
          >
            <XCircle className="w-4 h-4" />
            Deny
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// Completed tool result display - shows both input and output
function ToolResultCard({
  toolName,
  args,
  result,
}: {
  toolName: string;
  args?: Record<string, any>;
  result: string;
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  // Check if this specific result indicates denial (not just any mention of "denied")
  const isDenied = result === "Denied by user" ||
    result === "The tool call was denied." ||
    result.startsWith("Denied:");

  return (
    <Card className={cn(
      "border",
      isDenied
        ? "border-red-300 bg-red-50/50 dark:bg-red-950/10"
        : "border-green-300 bg-green-50/50 dark:bg-green-950/10"
    )}>
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full text-left"
      >
        <CardHeader className="pb-2 py-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              {isDenied ? (
                <XCircle className="w-4 h-4 text-red-500" />
              ) : (
                <CheckCircle2 className="w-4 h-4 text-green-500" />
              )}
              <span className="text-sm font-medium">
                {isDenied ? "Action Denied" : "Action Completed"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs">{toolName}</Badge>
              <span className="text-xs text-muted-foreground">{isExpanded ? "▼" : "▶"}</span>
            </div>
          </div>
        </CardHeader>
      </button>
      {isExpanded && (
        <CardContent className="pt-0 pb-3 space-y-2">
          {/* Show tool input/args */}
          {args && Object.keys(args).length > 0 && (
            <div className="text-sm">
              <Label className="text-muted-foreground text-xs">Input</Label>
              {args.to && (
                <p className="text-xs"><span className="text-muted-foreground">To:</span> {args.to}</p>
              )}
              {args.body && (
                <p className="text-xs mt-1 p-2 bg-background rounded border whitespace-pre-wrap">
                  {args.body}
                </p>
              )}
              {!args.to && !args.body && (
                <pre className="text-xs overflow-x-auto bg-background p-2 rounded border mt-1">
                  {JSON.stringify(args, null, 2)}
                </pre>
              )}
            </div>
          )}
          {/* Show result */}
          <div className="text-sm">
            <Label className="text-muted-foreground text-xs">Result</Label>
            <p className={cn(
              "text-xs mt-1",
              isDenied ? "text-red-600 dark:text-red-400" : "text-green-600 dark:text-green-400"
            )}>
              {result}
            </p>
          </div>
        </CardContent>
      )}
    </Card>
  );
}

// Generic tool display
function ToolInvocationDisplay({ toolName, args, result }: { toolName: string; args?: any; result?: any }) {
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-muted/20">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-4 py-2 text-left flex items-center justify-between hover:bg-muted/50 transition-colors"
      >
        <span className="text-sm font-medium flex items-center gap-2">
          <Zap className="w-4 h-4" />
          Tool: <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{toolName}</code>
        </span>
        <span className="text-xs text-muted-foreground">{isExpanded ? "▼" : "▶"}</span>
      </button>
      {isExpanded && (
        <div className="px-4 py-3 bg-muted/30 border-t border-border space-y-2">
          {args && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">Input:</div>
              <pre className="text-xs overflow-x-auto bg-background p-2 rounded border">
                {JSON.stringify(args, null, 2)}
              </pre>
            </div>
          )}
          {result && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">Output:</div>
              <pre className="text-xs overflow-x-auto bg-background p-2 rounded border">
                {typeof result === "string" ? result : JSON.stringify(result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface ConversationSummary {
  conversation_id: string;
  preview: string;
  has_pending: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export default function HITLAgentChatPage() {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // Get conversation ID from URL or generate new one
  const urlConversationId = searchParams.get("id");
  const [conversationId, setConversationId] = useState<string>(() => urlConversationId || crypto.randomUUID());

  const [input, setInput] = useState("");
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);
  const [isProcessingApproval, setIsProcessingApproval] = useState(false);
  const [conversationHistory, setConversationHistory] = useState<ConversationSummary[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [isLoadingConversation, setIsLoadingConversation] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Fetch conversation history
  const fetchHistory = useCallback(async () => {
    if (!isSignedIn) return;
    setIsLoadingHistory(true);
    try {
      const token = await getToken();
      const res = await fetch("/api/ai-demos/hitl-agent/conversations/history", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setConversationHistory(data.conversations || []);
      }
    } catch (err) {
      console.error("Failed to fetch history:", err);
    } finally {
      setIsLoadingHistory(false);
    }
  }, [isSignedIn, getToken]);

  // Delete a conversation
  const deleteConversationHandler = useCallback(async (convId: string, e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent triggering loadConversation
    if (!isSignedIn) return;

    if (!confirm("Are you sure you want to delete this conversation?")) return;

    try {
      const token = await getToken();
      const res = await fetch(`/api/ai-demos/hitl-agent/conversation/${convId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });

      if (res.ok) {
        // Remove from local state
        setConversationHistory((prev) => prev.filter((c) => c.conversation_id !== convId));

        // If we deleted the current conversation, start a new one
        if (convId === conversationId) {
          startNewConversation();
        }
      } else {
        console.error("Failed to delete conversation");
      }
    } catch (err) {
      console.error("Failed to delete conversation:", err);
    }
  }, [isSignedIn, getToken, conversationId]);

  useEffect(() => {
    if (historyOpen) {
      fetchHistory();
    }
  }, [historyOpen, fetchHistory]);

  // Load conversation from URL on mount or when URL changes
  const loadConversationFromUrl = useCallback(async (convId: string) => {
    if (!isSignedIn) return;
    setIsLoadingConversation(true);

    try {
      const token = await getToken();
      const res = await fetch(`/api/ai-demos/hitl-agent/conversation/${convId}/messages`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (!res.ok) {
        console.error("Failed to load conversation from URL");
        // Clear invalid conversation ID from URL
        navigate("/hitl-agent-chat", { replace: true });
        setIsLoadingConversation(false);
        return;
      }

      const data = await res.json();
      setConversationId(convId);

      // We need to set messages after useChat is ready
      // Store the loaded messages to set after transport is ready
      // Keep isLoadingConversation=true until messages are applied in the effect below
      pendingMessagesRef.current = data.messages || [];
      pendingApprovalRef.current = convertPendingFromApi(data.pending);
    } catch (err) {
      console.error("Failed to load conversation:", err);
      navigate("/hitl-agent-chat", { replace: true });
      setIsLoadingConversation(false);
    }
    // Note: Don't set isLoadingConversation=false here - it's set when messages are applied
  }, [isSignedIn, getToken, navigate]);

  // Refs to store pending data when loading from URL
  const pendingMessagesRef = useRef<any[] | null>(null);
  const pendingApprovalRef = useRef<PendingApproval | null>(null);
  // Track which conversation ID has been loaded to prevent re-loading
  const loadedConversationIdRef = useRef<string | null>(null);

  // Load conversation from URL on mount
  useEffect(() => {
    // Only load if we have a URL conversation ID, user is signed in,
    // and we haven't already loaded this conversation
    if (urlConversationId && isSignedIn && loadedConversationIdRef.current !== urlConversationId) {
      loadedConversationIdRef.current = urlConversationId;
      loadConversationFromUrl(urlConversationId);
    }
  }, [urlConversationId, isSignedIn, loadConversationFromUrl]);

  // Create transport with auth - needs to be a ref to handle async token
  const transportRef = useRef<DefaultChatTransport<any> | null>(null);
  // Use a version counter to track when transport is ready for a new conversation
  const [transportVersion, setTransportVersion] = useState(0);

  // Store getToken in a ref so transport can always get a fresh token
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  useEffect(() => {
    if (!isSignedIn) return;

    // Use a function for headers to get fresh token on each request
    // This prevents "User is not signed in" errors when token expires
    transportRef.current = new DefaultChatTransport({
      api: "/api/ai-demos/hitl-agent/chat",
      // headers as a function is called fresh for each request
      headers: async () => {
        const freshToken = await getTokenRef.current();
        return {
          Authorization: `Bearer ${freshToken}`,
        };
      },
      prepareSendMessagesRequest: ({ messages, body, trigger }) => {
        // Transform messages to proper format
        // Backend extracts only the last message and loads history from DB
        const transformedMessages = messages.map((msg: any) => {
          if (msg.parts) {
            return { id: msg.id || crypto.randomUUID(), role: msg.role, parts: msg.parts };
          }
          return {
            id: msg.id || crypto.randomUUID(),
            role: msg.role,
            parts: [{ type: "text", text: msg.content || "" }],
          };
        });

        return {
          body: {
            trigger,
            id: conversationId,
            messages: transformedMessages,
            ...body,
          },
        };
      },
    });
    // Increment version to trigger the effect that applies pending messages
    setTransportVersion((v) => v + 1);
  }, [isSignedIn, conversationId]);

  const { messages, sendMessage, status, stop, setMessages } = useChat({
    id: conversationId,
    transport: transportRef.current || new DefaultChatTransport({ api: "/api/ai-demos/hitl-agent/chat" }),
  } as any);

  // Apply pending messages from URL load once transport is ready
  // This effect runs when transportVersion changes OR when setMessages becomes available
  useEffect(() => {
    // Need transportVersion > 0 to ensure transport is set up
    // Also check if we have pending messages to apply
    if (transportVersion > 0 && pendingMessagesRef.current && setMessages) {
      console.log("Applying pending messages:", pendingMessagesRef.current.length);
      setMessages(pendingMessagesRef.current);
      if (pendingApprovalRef.current) {
        setPendingApproval(pendingApprovalRef.current);
      }
      // Clear refs
      pendingMessagesRef.current = null;
      pendingApprovalRef.current = null;
      // Now we can hide the loading state
      setIsLoadingConversation(false);
    }
  }, [transportVersion, setMessages]);

  // Poll to apply pending messages - handles race condition on initial load
  // where fetch completes after transport is ready
  useEffect(() => {
    if (!isLoadingConversation) return;

    const tryApplyPendingMessages = () => {
      if (pendingMessagesRef.current && setMessages) {
        console.log("Applying pending messages (poll):", pendingMessagesRef.current.length);
        setMessages(pendingMessagesRef.current);
        if (pendingApprovalRef.current) {
          setPendingApproval(pendingApprovalRef.current);
        }
        pendingMessagesRef.current = null;
        pendingApprovalRef.current = null;
        setIsLoadingConversation(false);
        return true;
      }
      return false;
    };

    // Try immediately
    if (tryApplyPendingMessages()) return;

    // Poll every 100ms while loading
    const interval = setInterval(() => {
      if (tryApplyPendingMessages()) {
        clearInterval(interval);
      }
    }, 100);

    // Cleanup after 5 seconds max
    const timeout = setTimeout(() => {
      clearInterval(interval);
      if (isLoadingConversation) {
        console.error("Timed out waiting for messages to load");
        setIsLoadingConversation(false);
      }
    }, 5000);

    return () => {
      clearInterval(interval);
      clearTimeout(timeout);
    };
  }, [isLoadingConversation, setMessages]);

  const [messagesContainerRef, messagesEndRef] = useScrollToBottom<HTMLDivElement>();

  // Extract pending approval from the latest assistant message
  useEffect(() => {
    if (status !== "ready") return;

    const lastAssistantMsg = [...messages].reverse().find((m) => m.role === "assistant");
    if (!lastAssistantMsg) return;

    // Check for tool invocations in parts
    const parts = (lastAssistantMsg as any).parts || [];

    // Find pending tool calls
    // Vercel AI SDK format from PydanticAI:
    // - type: "tool-{toolName}" (e.g., "tool-send_sms")
    // - state: "input-available" (pending) or "output-available" (completed)
    // - toolCallId, input, output
    for (const part of parts) {
      // Check if this is a tool part (type starts with "tool-")
      if (!part.type?.startsWith("tool-")) continue;

      // Extract tool name from type (e.g., "tool-send_sms" -> "send_sms")
      const toolName = part.type.replace("tool-", "");

      // Check if it's pending (input-available, no output yet)
      const isPending = part.state === "input-available" && part.output === undefined;

      if (isPending && part.toolCallId && part.input) {
        console.log("Found pending tool call:", { toolCallId: part.toolCallId, toolName, input: part.input });
        setPendingApproval({
          toolCallId: part.toolCallId,
          toolName: toolName,
          args: part.input,
        });
        return;
      }
    }

    // No pending approval found
    setPendingApproval(null);
  }, [messages, status]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [input]);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (input.trim() && status !== "submitted" && status !== "streaming") {
      // Reset approval state for new message
      setPendingApproval(null);
      sendMessage({ role: "user", parts: [{ type: "text", text: input }] });
      setInput("");
    }
  };

  const handleSuggestedPrompt = (prompt: string) => {
    setPendingApproval(null);
    sendMessage({ role: "user", parts: [{ type: "text", text: prompt }] });
  };

  const handleApprovalComplete = useCallback((result: any) => {
    setPendingApproval(null);

    // Build a map of tool_call_id -> approved status from the response
    const decisionMap = new Map<string, boolean>();
    if (result.decisions) {
      for (const d of result.decisions) {
        decisionMap.set(d.tool_call_id, d.approved);
      }
    }

    // Update the last message to include the tool result, then add the agent's response
    setMessages((prev: any[]) => {
      const updated = [...prev];

      // Find the last assistant message (with the tool call) and update it to show completed state
      const lastAssistantIdx = updated.findLastIndex((m: any) => m.role === "assistant");
      if (lastAssistantIdx >= 0) {
        const lastMsg = updated[lastAssistantIdx];
        // Update tool parts to show completed state with proper tool name extraction
        if (lastMsg.parts) {
          lastMsg.parts = lastMsg.parts.map((part: any) => {
            if (part.type?.startsWith("tool-") && part.state === "input-available") {
              const toolName = part.type.replace("tool-", "");
              // Look up approval status from decisions, default to true if not found
              const toolCallId = part.toolCallId;
              const wasApproved = toolCallId ? decisionMap.get(toolCallId) ?? true : true;
              return {
                ...part,
                state: "output-available",
                output: wasApproved
                  ? `${toolName} completed successfully`
                  : "Denied by user",
              };
            }
            return part;
          });
        }
        updated[lastAssistantIdx] = { ...lastMsg };
      }

      // Add the agent's response as a new message if there's output
      // Note: Don't include 'content' field - PydanticAI's adapter rejects extra fields
      if (result.output) {
        updated.push({
          id: crypto.randomUUID(),
          role: "assistant" as const,
          parts: [{ type: "text", text: result.output }],
        });
      }

      return updated;
    });
  }, [setMessages]);

  // Process messages to extract content and tool invocations
  const processMessage = (message: any) => {
    let textContent = "";
    if (message.content && typeof message.content === "string") {
      textContent = message.content;
    } else if (message.parts && Array.isArray(message.parts)) {
      const textParts = message.parts.filter((part: any) => part.type === "text");
      textContent = textParts.map((p: any) => p.text).join("");
    }

    // Extract tool invocations that have results (completed)
    // This handles both:
    // 1. Tools completed during this session (state: "output-available", has output)
    // 2. Tools loaded from history that were already completed (has output/result)
    // 3. Dynamic tools from PydanticAI (type: "dynamic-tool")
    let completedTools: any[] = [];
    if (message.parts && Array.isArray(message.parts)) {
      completedTools = message.parts
        .filter((part: any) => {
          // Check if this is a tool part
          // Formats: "tool-{name}", "tool-invocation", "tool-call", "dynamic-tool", or has toolCallId/tool_call_id
          const isToolPart = part.type?.startsWith("tool-") ||
                            part.type === "tool-invocation" ||
                            part.type === "tool-call" ||
                            part.type === "dynamic-tool" ||
                            part.toolCallId ||
                            part.tool_call_id;
          if (!isToolPart) return false;

          // A tool is "completed" if:
          // 1. It has output/result data, OR
          // 2. Its state is "output-available" (even if output is just a string like "completed")
          const hasOutput = part.result !== undefined || part.output !== undefined;
          const isOutputAvailable = part.state === "output-available";

          return hasOutput || isOutputAvailable;
        })
        .map((part: any) => {
          // Extract tool name from various formats:
          // - tool_name (from dynamic-tool)
          // - toolName (from tool-invocation)
          // - name (generic)
          // - type "tool-{name}" -> "{name}"
          let toolName = part.tool_name || part.toolName || part.name;
          if (!toolName && part.type?.startsWith("tool-") && part.type !== "tool-invocation" && part.type !== "tool-call") {
            toolName = part.type.replace("tool-", "");
          }

          // Parse input if it's a JSON string (from dynamic-tool format)
          let args = part.args || part.input;
          if (typeof args === "string") {
            try {
              args = JSON.parse(args);
            } catch {
              // Keep as string if not valid JSON
            }
          }

          return {
            toolCallId: part.toolCallId || part.tool_call_id || part.id,
            toolName: toolName || "unknown",
            args,
            result: part.result || part.output || "Completed",
          };
        });
    }

    return { textContent, completedTools };
  };

  const formatDate = (dateString: string | null) => {
    if (!dateString) return "";
    const date = new Date(dateString);
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };

  const startNewConversation = () => {
    const newId = crypto.randomUUID();
    setConversationId(newId);
    setMessages([]);
    setPendingApproval(null);
    setHistoryOpen(false);
    // Reset the loaded conversation tracker
    loadedConversationIdRef.current = null;
    // Update URL without the id param for new conversations
    navigate("/hitl-agent-chat", { replace: true });
  };

  const loadConversation = useCallback(async (convId: string) => {
    if (!isSignedIn) return;
    setIsLoadingConversation(true);

    try {
      const token = await getToken();
      const res = await fetch(`/api/ai-demos/hitl-agent/conversation/${convId}/messages`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (!res.ok) {
        console.error("Failed to load conversation");
        setIsLoadingConversation(false);
        return;
      }

      const data = await res.json();

      // Update URL with conversation ID
      navigate(`/hitl-agent-chat?id=${convId}`, { replace: true });

      // Mark this conversation as loaded to prevent the URL effect from re-triggering
      loadedConversationIdRef.current = convId;

      // Set the conversation ID - this will trigger transport recreation
      setConversationId(convId);

      // Store messages in refs to apply after transport is ready
      // Keep isLoadingConversation=true until messages are applied in the effect
      pendingMessagesRef.current = data.messages || [];
      pendingApprovalRef.current = convertPendingFromApi(data.pending);

      setHistoryOpen(false);
    } catch (err) {
      console.error("Failed to load conversation:", err);
      setIsLoadingConversation(false);
    }
    // Note: isLoadingConversation is set to false when messages are applied in the effect
  }, [isSignedIn, getToken, navigate]);

  // Loading conversation state (shown inside AuthGuard)
  if (isLoadingConversation) {
    return (
      <AuthGuard
        message="Chat with an AI agent that requires approval for sensitive actions"
        icon={<MessageSquare className="w-16 h-16 text-muted-foreground" />}
      >
        <div className="flex flex-col items-center justify-center h-[calc(100dvh-52px)] gap-4">
          <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
          <p className="text-muted-foreground">Loading conversation...</p>
        </div>
      </AuthGuard>
    );
  }

  return (
    <AuthGuard
      message="Chat with an AI agent that requires approval for sensitive actions"
      icon={<MessageSquare className="w-16 h-16 text-muted-foreground" />}
    >
    <div className="flex flex-col min-w-0 h-[calc(100dvh-52px)] bg-background">
      {/* Header with history */}
      <div className="flex items-center justify-between px-4 py-2 border-b">
        <div className="flex items-center gap-2">
          <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="sm" className="gap-2">
                <History className="w-4 h-4" />
                History
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-80">
              <SheetHeader>
                <SheetTitle>Conversation History</SheetTitle>
              </SheetHeader>
              <div className="mt-4 space-y-2">
                <Button
                  variant="outline"
                  className="w-full justify-start gap-2"
                  onClick={startNewConversation}
                >
                  <Plus className="w-4 h-4" />
                  New Conversation
                </Button>
                <div className="h-px bg-border my-2" />
                {isLoadingHistory ? (
                  <div className="flex justify-center py-4">
                    <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
                  </div>
                ) : conversationHistory.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-4">
                    No previous conversations
                  </p>
                ) : (
                  conversationHistory.map((conv) => (
                    <div
                      key={conv.conversation_id}
                      className={cn(
                        "group flex items-center gap-1 p-2 rounded-lg hover:bg-muted transition-colors",
                        conv.conversation_id === conversationId && "bg-muted"
                      )}
                    >
                      <button
                        onClick={() => loadConversation(conv.conversation_id)}
                        className="flex-1 text-left min-w-0"
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-sm truncate flex-1">
                            {conv.preview || "Empty conversation"}
                          </span>
                          {conv.has_pending && (
                            <Badge variant="outline" className="ml-2 text-xs">
                              Pending
                            </Badge>
                          )}
                        </div>
                        <div className="flex items-center gap-1 text-xs text-muted-foreground mt-1">
                          <Clock className="w-3 h-3" />
                          {formatDate(conv.updated_at)}
                        </div>
                      </button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
                        onClick={(e) => deleteConversationHandler(conv.conversation_id, e)}
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  ))
                )}
              </div>
            </SheetContent>
          </Sheet>
        </div>
        <Button variant="ghost" size="sm" className="gap-2" onClick={startNewConversation}>
          <Plus className="w-4 h-4" />
          New Chat
        </Button>
      </div>

      {/* Messages */}
      <div ref={messagesContainerRef} className="flex flex-col min-w-0 gap-6 flex-1 overflow-y-scroll pt-4">
        {messages.length === 0 ? (
          <div className="mx-auto w-full max-w-3xl px-4">
            <div className="flex flex-col items-center gap-4 py-8">
              <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center">
                <MessageSquare className="w-8 h-8 text-primary" />
              </div>
              <h2 className="text-2xl font-bold">HITL Agent Chat</h2>
              <p className="text-sm text-muted-foreground text-center max-w-md">
                This agent can send SMS messages on your behalf. When it proposes an action,
                you'll see the details and can approve, edit, or deny before it executes.
              </p>
              <div className="text-xs text-muted-foreground text-center space-y-2 mt-4 max-w-md">
                <p>
                  <strong>Human-in-the-Loop</strong>: Tools with{" "}
                  <code className="bg-muted px-1 rounded">requires_approval=True</code> pause
                  for your decision before executing.
                </p>
              </div>
            </div>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-3xl px-4 space-y-6">
            {messages.map((message, index) => {
              const { textContent, completedTools } = processMessage(message);
              const isLastAssistant = message.role === "assistant" && index === messages.length - 1;

              return (
                <div
                  key={message.id || index}
                  className={cn(
                    "flex gap-3",
                    message.role === "user" ? "justify-end" : "justify-start"
                  )}
                >
                  {message.role === "assistant" && (
                    <div className="flex-shrink-0">
                      <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center">
                        <MessageSquare className="w-4 h-4 text-primary-foreground" />
                      </div>
                    </div>
                  )}

                  <div className={cn("flex flex-col gap-2 max-w-[85%]", message.role === "user" && "items-end")}>
                    {message.role === "user" ? (
                      <div className="bg-primary text-primary-foreground rounded-2xl px-4 py-2.5 shadow-sm">
                        <p className="text-sm whitespace-pre-wrap">{textContent}</p>
                      </div>
                    ) : (
                      <>
                        {/* Text content */}
                        {textContent && (
                          <div className="bg-muted/50 rounded-2xl px-4 py-2.5 shadow-sm">
                            <div className="text-sm prose prose-sm dark:prose-invert max-w-none">
                              <Markdown>{textContent}</Markdown>
                            </div>
                          </div>
                        )}

                        {/* Completed tool results */}
                        {completedTools.map((tool) => (
                          <ToolResultCard
                            key={tool.toolCallId}
                            toolName={tool.toolName}
                            args={tool.args}
                            result={typeof tool.result === "string" ? tool.result : JSON.stringify(tool.result)}
                          />
                        ))}

                        {/* Pending approval (only on last assistant message) */}
                        {isLastAssistant && pendingApproval && (
                          <ToolApprovalCard
                            pending={pendingApproval}
                            conversationId={conversationId}
                            onApprovalComplete={handleApprovalComplete}
                            isProcessing={isProcessingApproval}
                            getToken={getToken}
                          />
                        )}
                      </>
                    )}
                  </div>

                  {message.role === "user" && (
                    <div className="flex-shrink-0">
                      <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-sm font-medium">
                        U
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
            <div ref={messagesEndRef} className="shrink-0 min-w-[24px] min-h-[24px]" />
          </div>
        )}
      </div>

      {/* Input Area */}
      <form
        onSubmit={handleSubmit}
        className="flex mx-auto px-4 bg-background pb-4 md:pb-6 gap-2 w-full md:max-w-3xl"
      >
        {messages.length === 0 ? (
          <div className="flex flex-col gap-4 w-full">
            <p className="text-sm text-muted-foreground text-center">
              Try a suggestion or type your own request
            </p>
            <div className="grid sm:grid-cols-3 gap-2">
              {suggestedPrompts.map((suggestion, index) => (
                <Button
                  key={index}
                  variant="ghost"
                  type="button"
                  onClick={() => handleSuggestedPrompt(suggestion.prompt)}
                  className="text-left border rounded-xl px-4 py-3.5 text-sm h-auto justify-start"
                >
                  {suggestion.title}
                </Button>
              ))}
            </div>
            <div className="flex gap-2 items-end">
              <Textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmit();
                  }
                }}
                placeholder="Ask the agent to send a message..."
                className="min-h-[24px] max-h-[calc(75dvh)] overflow-hidden resize-none rounded-xl text-base bg-muted"
                rows={2}
                disabled={status === "submitted" || status === "streaming"}
              />
              <Button
                type="submit"
                size="icon"
                disabled={!input.trim() || status === "submitted" || status === "streaming"}
                className="h-11 w-11 shrink-0 rounded-xl"
              >
                <Send className="w-5 h-5" />
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex gap-2 items-end w-full">
            <Textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSubmit();
                }
              }}
              placeholder={pendingApproval ? "Approve or deny the action above first..." : "Continue the conversation..."}
              className="min-h-[24px] max-h-[calc(75dvh)] overflow-hidden resize-none rounded-xl text-base bg-muted"
              rows={2}
              disabled={status === "submitted" || status === "streaming" || !!pendingApproval}
            />
            {status === "streaming" ? (
              <Button
                type="button"
                onClick={stop}
                size="icon"
                variant="outline"
                className="h-11 w-11 shrink-0 rounded-xl"
              >
                <StopCircle className="w-5 h-5" />
              </Button>
            ) : (
              <Button
                type="submit"
                size="icon"
                disabled={!input.trim() || status === "submitted" || !!pendingApproval}
                className="h-11 w-11 shrink-0 rounded-xl"
              >
                <Send className="w-5 h-5" />
              </Button>
            )}
          </div>
        )}
      </form>
    </div>
    </AuthGuard>
  );
}
