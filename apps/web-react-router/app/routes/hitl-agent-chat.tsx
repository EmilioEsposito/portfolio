import type { Route } from "./+types/hitl-agent-chat";
import { useState, useRef, useEffect, useCallback } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useAuth } from "@clerk/react-router";
import { Button } from "~/components/ui/button";
import { Textarea } from "~/components/ui/textarea";
import { useScrollToBottom } from "~/hooks/use-scroll-to-bottom";
import { cn } from "~/lib/utils";
import { Markdown } from "~/components/markdown";
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
  const [editedBody, setEditedBody] = useState(pending.args.body || "");
  const [processing, setProcessing] = useState(false);

  const handleApproval = async (approved: boolean) => {
    setProcessing(true);
    try {
      const token = await getToken();
      const overrideArgs = isEditing && editedBody !== pending.args.body
        ? { body: editedBody }
        : undefined;

      const url = `/api/ai/hitl-agent/conversation/${conversationId}/approve`;
      console.log("Approval URL:", url, "conversationId:", conversationId);

      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          tool_call_id: pending.toolCallId,
          approved,
          override_args: overrideArgs,
          reason: approved ? undefined : "Denied by user",
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
        {pending.args.to && (
          <div className="text-sm">
            <Label className="text-muted-foreground text-xs">To</Label>
            <p className="font-mono">{pending.args.to}</p>
          </div>
        )}
        {!pending.args.to && (
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
              {editedBody !== pending.args.body && (
                <p className="text-xs text-amber-600 dark:text-amber-400">
                  Modified - will override AI's suggestion
                </p>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setIsEditing(false);
                  setEditedBody(pending.args.body || "");
                }}
                disabled={isDisabled}
              >
                Cancel edit
              </Button>
            </div>
          ) : (
            <p className="p-2 bg-background rounded border whitespace-pre-wrap">
              {pending.args.body}
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

// Completed tool result display
function ToolResultCard({ toolName, result }: { toolName: string; result: string }) {
  return (
    <Card className="border-2 border-green-500 bg-green-50 dark:bg-green-950/20">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-5 h-5 text-green-500" />
            <CardTitle className="text-sm font-medium">Action Completed</CardTitle>
          </div>
          <Badge variant="secondary">{toolName}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">{result}</p>
      </CardContent>
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
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string>(() => crypto.randomUUID());
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);
  const [approvalResult, setApprovalResult] = useState<string | null>(null);
  const [isProcessingApproval, setIsProcessingApproval] = useState(false);
  const [conversationHistory, setConversationHistory] = useState<ConversationSummary[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Fetch conversation history
  const fetchHistory = useCallback(async () => {
    if (!isSignedIn) return;
    setIsLoadingHistory(true);
    try {
      const token = await getToken();
      const res = await fetch("/api/ai/hitl-agent/conversations/history", {
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

  useEffect(() => {
    if (historyOpen) {
      fetchHistory();
    }
  }, [historyOpen, fetchHistory]);

  // Create transport with auth - needs to be a ref to handle async token
  const transportRef = useRef<DefaultChatTransport<any> | null>(null);
  const [transportReady, setTransportReady] = useState(false);

  useEffect(() => {
    if (!isSignedIn) return;

    const setupTransport = async () => {
      const token = await getToken();
      transportRef.current = new DefaultChatTransport({
        api: "/api/ai/hitl-agent/chat",
        headers: { Authorization: `Bearer ${token}` },
        prepareSendMessagesRequest: ({ messages, body, trigger }) => {
          const transformedMessages = messages.map((msg: any) => {
            if (msg.parts) return msg;
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
      setTransportReady(true);
    };

    setupTransport();
  }, [isSignedIn, getToken, conversationId]);

  const { messages, sendMessage, status, stop, setMessages } = useChat({
    id: conversationId,
    transport: transportRef.current || new DefaultChatTransport({ api: "/api/ai/hitl-agent/chat" }),
  } as any);

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
      setApprovalResult(null);
      sendMessage({ role: "user", parts: [{ type: "text", text: input }] });
      setInput("");
    }
  };

  const handleSuggestedPrompt = (prompt: string) => {
    setPendingApproval(null);
    setApprovalResult(null);
    sendMessage({ role: "user", parts: [{ type: "text", text: prompt }] });
  };

  const handleApprovalComplete = useCallback((result: any) => {
    setPendingApproval(null);
    if (result.output) {
      setApprovalResult(result.output);
    }
    // Reset conversation for next interaction
    // setConversationId(crypto.randomUUID());
  }, []);

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
    let completedTools: any[] = [];
    if (message.parts && Array.isArray(message.parts)) {
      completedTools = message.parts
        .filter((part: any) => {
          // Tool invocation with result means it's completed
          return (
            (part.type === "tool-invocation" || part.type === "tool-call" || part.toolCallId) &&
            (part.result || part.output)
          );
        })
        .map((part: any) => ({
          toolCallId: part.toolCallId || part.id,
          toolName: part.toolName || part.name,
          args: part.args || part.input,
          result: part.result || part.output,
        }));
    }

    return { textContent, completedTools };
  };

  const formatDate = (dateString: string | null) => {
    if (!dateString) return "";
    const date = new Date(dateString);
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };

  const startNewConversation = () => {
    setConversationId(crypto.randomUUID());
    setMessages([]);
    setPendingApproval(null);
    setApprovalResult(null);
    setHistoryOpen(false);
  };

  // Auth check
  if (!isLoaded) {
    return (
      <div className="flex items-center justify-center h-[calc(100dvh-52px)]">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!isSignedIn) {
    return (
      <div className="flex flex-col items-center justify-center h-[calc(100dvh-52px)] gap-4">
        <MessageSquare className="w-12 h-12 text-muted-foreground" />
        <p className="text-muted-foreground">Please sign in to use HITL Agent Chat</p>
      </div>
    );
  }

  return (
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
                    <button
                      key={conv.conversation_id}
                      onClick={() => {
                        setConversationId(conv.conversation_id);
                        setHistoryOpen(false);
                        // TODO: Load conversation messages
                      }}
                      className={cn(
                        "w-full text-left p-2 rounded-lg hover:bg-muted transition-colors",
                        conv.conversation_id === conversationId && "bg-muted"
                      )}
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

                        {/* Approval result (after approval completes) */}
                        {isLastAssistant && approvalResult && !pendingApproval && (
                          <ToolResultCard toolName="send_sms" result={approvalResult} />
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
  );
}
