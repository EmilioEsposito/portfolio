import type { Route } from "./+types/sernia-chat";
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
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "~/components/ui/tabs";
import {
  Building,
  StopCircle,
  Send,
  Loader2,
  History,
  Plus,
  Clock,
  Trash2,
  RefreshCw,
} from "lucide-react";
import { Badge } from "~/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "~/components/ui/sheet";
import {
  ToolApprovalCard,
  ToolResultCard,
  convertPendingFromApi,
  type PendingApproval,
} from "~/components/chat/tool-cards";
import { processMessage } from "~/components/chat/process-message";

const API_BASE = "/api/sernia-ai";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Sernia AI" },
    {
      name: "description",
      content:
        "AI assistant for Sernia Capital — manages tasks, searches info, sends messages, and builds knowledge over time.",
    },
  ];
}

const suggestedPrompts = [
  {
    title: "What's in your memory?",
    prompt: "What's in your memory?",
  },
  {
    title: "What are today's notes?",
    prompt: "What are today's notes?",
  },
  {
    title: "Search for recent property info",
    prompt: "Search for recent property info",
  },
];

interface ConversationSummary {
  conversation_id: string;
  preview: string;
  has_pending: boolean;
  created_at: string | null;
  updated_at: string | null;
}

// ---------------------------------------------------------------------------
// Inner chat component — remounts on conversation switch via `key` prop
// ---------------------------------------------------------------------------

function ChatView({
  conversationId,
  initialMessages,
  initialPending,
  getToken,
}: {
  conversationId: string;
  initialMessages: any[];
  initialPending: PendingApproval | null;
  getToken: () => Promise<string | null>;
}) {
  const [pendingApproval, setPendingApproval] =
    useState<PendingApproval | null>(initialPending);
  const [isProcessingApproval] = useState(false);
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [messagesContainerRef, messagesEndRef] =
    useScrollToBottom<HTMLDivElement>();

  // Transport is created once per mount (conversationId is stable for this instance)
  const transport = useRef(
    new DefaultChatTransport({
      api: `${API_BASE}/chat`,
      headers: async () => {
        const token = await getToken();
        return { Authorization: `Bearer ${token}` };
      },
      prepareSendMessagesRequest: ({ messages, body, trigger }) => {
        const transformedMessages = messages.map((msg: any) => {
          if (msg.parts) {
            return {
              id: msg.id || crypto.randomUUID(),
              role: msg.role,
              parts: msg.parts,
            };
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
    })
  ).current;

  const { messages, sendMessage, status, stop, setMessages } = useChat({
    id: conversationId,
    initialMessages,
    transport,
  } as any);

  // Extract pending approval from latest assistant message
  useEffect(() => {
    if (status !== "ready") return;

    const lastAssistantMsg = [...messages]
      .reverse()
      .find((m) => m.role === "assistant");
    if (!lastAssistantMsg) return;

    const parts = (lastAssistantMsg as any).parts || [];
    for (const part of parts) {
      if (!part.type?.startsWith("tool-")) continue;
      const toolName = part.type.replace("tool-", "");
      const isPending =
        part.state === "input-available" && part.output === undefined;

      if (isPending && part.toolCallId && part.input) {
        setPendingApproval({
          toolCallId: part.toolCallId,
          toolName,
          args: part.input,
        });
        return;
      }
    }
    setPendingApproval(null);
  }, [messages, status]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [input]);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (input.trim() && status !== "submitted" && status !== "streaming") {
      setPendingApproval(null);
      sendMessage({ role: "user", parts: [{ type: "text", text: input }] });
      setInput("");
    }
  };

  const handleSuggestedPrompt = (prompt: string) => {
    setPendingApproval(null);
    sendMessage({ role: "user", parts: [{ type: "text", text: prompt }] });
  };

  const handleApprovalComplete = useCallback(
    (result: any) => {
      setPendingApproval(null);

      const decisionMap = new Map<string, boolean>();
      if (result.decisions) {
        for (const d of result.decisions) {
          decisionMap.set(d.tool_call_id, d.approved);
        }
      }

      // Backend returns actual tool results keyed by tool_call_id
      const toolResults: Record<string, string> = result.tool_results || {};

      setMessages((prev: any[]) => {
        const updated = [...prev];
        const lastAssistantIdx = updated.findLastIndex(
          (m: any) => m.role === "assistant"
        );
        if (lastAssistantIdx >= 0) {
          const lastMsg = updated[lastAssistantIdx];
          if (lastMsg.parts) {
            lastMsg.parts = lastMsg.parts.map((part: any) => {
              if (
                part.type?.startsWith("tool-") &&
                part.state === "input-available"
              ) {
                const wasApproved = part.toolCallId
                  ? (decisionMap.get(part.toolCallId) ?? true)
                  : true;
                const realResult = part.toolCallId
                  ? toolResults[part.toolCallId]
                  : undefined;
                return {
                  ...part,
                  state: "output-available",
                  output: wasApproved
                    ? realResult || "Completed"
                    : "Denied by user",
                };
              }
              return part;
            });
          }
          updated[lastAssistantIdx] = { ...lastMsg };
        }

        if (result.output) {
          updated.push({
            id: crypto.randomUUID(),
            role: "assistant" as const,
            parts: [{ type: "text", text: result.output }],
          });
        }

        return updated;
      });
    },
    [setMessages]
  );

  return (
    <>
      {/* Messages */}
      <div
        ref={messagesContainerRef}
        className="flex flex-col min-w-0 gap-6 flex-1 overflow-y-scroll pt-4"
      >
        {messages.length === 0 ? (
          <div className="mx-auto w-full max-w-3xl px-4">
            <div className="flex flex-col items-center gap-4 py-8">
              <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center">
                <Building className="w-8 h-8 text-primary" />
              </div>
              <h2 className="text-2xl font-bold">Sernia AI</h2>
              <p className="text-sm text-muted-foreground text-center max-w-md">
                Your AI assistant for Sernia Capital. Manages tasks, searches
                info, sends messages, and builds knowledge over time.
              </p>
            </div>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-3xl px-4 space-y-6">
            {messages.map((message, index) => {
              const { textContent, completedTools } = processMessage(message);
              const isLastAssistant =
                message.role === "assistant" &&
                index === messages.length - 1;

              return (
                <div
                  key={message.id || index}
                  className={cn(
                    "flex gap-3",
                    message.role === "user"
                      ? "justify-end"
                      : "justify-start"
                  )}
                >
                  {message.role === "assistant" && (
                    <div className="shrink-0">
                      <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center">
                        <Building className="w-4 h-4 text-primary-foreground" />
                      </div>
                    </div>
                  )}

                  <div
                    className={cn(
                      "flex flex-col gap-2 max-w-[85%]",
                      message.role === "user" && "items-end"
                    )}
                  >
                    {message.role === "user" ? (
                      <div className="bg-primary text-primary-foreground rounded-2xl px-4 py-2.5 shadow-sm">
                        <p className="text-sm whitespace-pre-wrap">
                          {textContent}
                        </p>
                      </div>
                    ) : (
                      <>
                        {textContent && (
                          <div className="bg-muted/50 rounded-2xl px-4 py-2.5 shadow-sm">
                            <div className="text-sm prose prose-sm dark:prose-invert max-w-none">
                              <Markdown>{textContent}</Markdown>
                            </div>
                          </div>
                        )}

                        {completedTools.map((tool) => (
                          <ToolResultCard
                            key={tool.toolCallId}
                            toolName={tool.toolName}
                            args={tool.args}
                            result={
                              typeof tool.result === "string"
                                ? tool.result
                                : JSON.stringify(tool.result)
                            }
                          />
                        ))}

                        {isLastAssistant && pendingApproval && (
                          <ToolApprovalCard
                            pending={pendingApproval}
                            conversationId={conversationId}
                            onApprovalComplete={handleApprovalComplete}
                            isProcessing={isProcessingApproval}
                            getToken={getToken}
                            apiBase={API_BASE}
                          />
                        )}
                      </>
                    )}
                  </div>

                  {message.role === "user" && (
                    <div className="shrink-0">
                      <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-sm font-medium">
                        U
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
            <div
              ref={messagesEndRef}
              className="shrink-0 min-w-[24px] min-h-[24px]"
            />
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
              {suggestedPrompts.map((suggestion, idx) => (
                <Button
                  key={idx}
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
                placeholder="Ask Sernia AI anything..."
                className="min-h-[24px] max-h-[calc(75dvh)] overflow-hidden resize-none rounded-xl text-base bg-muted"
                rows={2}
                disabled={
                  status === "submitted" || status === "streaming"
                }
              />
              <Button
                type="submit"
                size="icon"
                disabled={
                  !input.trim() ||
                  status === "submitted" ||
                  status === "streaming"
                }
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
              placeholder={
                pendingApproval
                  ? "Approve or deny the action above first..."
                  : "Ask Sernia AI anything..."
              }
              className="min-h-[24px] max-h-[calc(75dvh)] overflow-hidden resize-none rounded-xl text-base bg-muted"
              rows={2}
              disabled={
                status === "submitted" ||
                status === "streaming" ||
                !!pendingApproval
              }
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
                disabled={
                  !input.trim() ||
                  status === "submitted" ||
                  !!pendingApproval
                }
                className="h-11 w-11 shrink-0 rounded-xl"
              >
                <Send className="w-5 h-5" />
              </Button>
            )}
          </div>
        )}
      </form>
    </>
  );
}

// ---------------------------------------------------------------------------
// System Instructions admin view
// ---------------------------------------------------------------------------

interface InstructionSection {
  label: string;
  content: string;
}

const MODALITIES = ["web_chat", "sms", "email"] as const;

function SystemInstructionsView({
  getToken,
}: {
  getToken: () => Promise<string | null>;
}) {
  const [sections, setSections] = useState<InstructionSection[] | null>(null);
  const [model, setModel] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Mock overrides
  const [modality, setModality] = useState<string>("web_chat");
  const [userName, setUserName] = useState<string>("");

  const fetchInstructions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const token = await getToken();
      const params = new URLSearchParams({ modality });
      if (userName.trim()) params.set("user_name", userName.trim());
      const res = await fetch(
        `${API_BASE}/admin/system-instructions?${params}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      setSections(data.sections);
      setModel(data.model || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [getToken, modality, userName]);

  useEffect(() => {
    fetchInstructions();
  }, [fetchInstructions]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-4 py-6 space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">System Instructions</h2>
            <p className="text-sm text-muted-foreground">
              Resolved instructions as the model sees them.
              {model && (
                <>
                  {" "}
                  Model:{" "}
                  <code className="text-xs bg-muted px-1 rounded">
                    {model}
                  </code>
                </>
              )}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={fetchInstructions}
            disabled={loading}
            className="gap-1.5"
          >
            <RefreshCw
              className={cn("w-3.5 h-3.5", loading && "animate-spin")}
            />
            Refresh
          </Button>
        </div>

        {/* Mock context controls */}
        <div className="flex flex-wrap items-end gap-4 rounded-lg border bg-muted/30 p-4">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              Modality
            </label>
            <div className="flex gap-1">
              {MODALITIES.map((m) => (
                <Button
                  key={m}
                  variant={modality === m ? "default" : "outline"}
                  size="sm"
                  className="text-xs h-8"
                  onClick={() => setModality(m)}
                >
                  {m}
                </Button>
              ))}
            </div>
          </div>
          <div className="space-y-1.5 flex-1 min-w-[200px]">
            <label className="text-xs font-medium text-muted-foreground">
              User name override
            </label>
            <input
              value={userName}
              onChange={(e) => setUserName(e.target.value)}
              placeholder="(uses your Clerk name)"
              className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-xs shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 dark:bg-red-950/20 p-4 text-sm text-red-700 dark:text-red-400">
            {error}
          </div>
        )}

        {loading && !sections && (
          <div className="flex justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {sections?.map((section, idx) => (
          <div key={idx} className="space-y-2">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs font-mono">
                {section.label}
              </Badge>
            </div>
            <pre className="text-sm whitespace-pre-wrap bg-muted/50 rounded-lg p-4 border overflow-x-auto">
              {section.content}
            </pre>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Outer page component — manages conversation selection & history
// ---------------------------------------------------------------------------

export default function SerniaChatPage() {
  const { isSignedIn, getToken } = useAuth();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const urlConversationId = searchParams.get("id");
  const [conversationId, setConversationId] = useState<string>(
    () => urlConversationId || crypto.randomUUID()
  );

  // Loaded messages for the current conversation (null = new conversation)
  const [loadedMessages, setLoadedMessages] = useState<any[] | null>(
    urlConversationId ? null : []
  );
  const [loadedPending, setLoadedPending] =
    useState<PendingApproval | null>(null);

  const [conversationHistory, setConversationHistory] = useState<
    ConversationSummary[]
  >([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  // Fetch conversation history
  const fetchHistory = useCallback(async () => {
    if (!isSignedIn) return;
    setIsLoadingHistory(true);
    try {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/conversations/history`, {
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
    if (historyOpen) fetchHistory();
  }, [historyOpen, fetchHistory]);

  // Load conversation messages from API
  const loadConversation = useCallback(
    async (convId: string, opts?: { updateUrl?: boolean }) => {
      if (!isSignedIn) return;
      setLoadedMessages(null); // triggers loading state

      try {
        const token = await getToken();
        const res = await fetch(
          `${API_BASE}/conversation/${convId}/messages`,
          { headers: { Authorization: `Bearer ${token}` } }
        );

        if (!res.ok) {
          console.error("Failed to load conversation");
          navigate("/sernia-chat", { replace: true });
          setLoadedMessages([]);
          return;
        }

        const data = await res.json();
        setLoadedPending(convertPendingFromApi(data.pending));
        setConversationId(convId);
        setLoadedMessages(data.messages || []);
        setHistoryOpen(false);

        if (opts?.updateUrl !== false) {
          navigate(`/sernia-chat?id=${convId}`, { replace: true });
        }
      } catch (err) {
        console.error("Failed to load conversation:", err);
        navigate("/sernia-chat", { replace: true });
        setLoadedMessages([]);
      }
    },
    [isSignedIn, getToken, navigate]
  );

  // Load from URL on mount
  useEffect(() => {
    if (urlConversationId && isSignedIn) {
      loadConversation(urlConversationId, { updateUrl: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSignedIn]);

  // Delete a conversation
  const deleteConversation = useCallback(
    async (convId: string, e: React.MouseEvent) => {
      e.stopPropagation();
      if (!isSignedIn) return;
      if (!confirm("Are you sure you want to delete this conversation?"))
        return;

      try {
        const token = await getToken();
        const res = await fetch(`${API_BASE}/conversation/${convId}`, {
          method: "DELETE",
          headers: { Authorization: `Bearer ${token}` },
        });

        if (res.ok) {
          setConversationHistory((prev) =>
            prev.filter((c) => c.conversation_id !== convId)
          );
          if (convId === conversationId) {
            startNewConversation();
          }
        }
      } catch (err) {
        console.error("Failed to delete conversation:", err);
      }
    },
    [isSignedIn, getToken, conversationId]
  );

  const startNewConversation = () => {
    const newId = crypto.randomUUID();
    setConversationId(newId);
    setLoadedMessages([]);
    setLoadedPending(null);
    setHistoryOpen(false);
    navigate("/sernia-chat", { replace: true });
  };

  const formatDate = (dateString: string | null) => {
    if (!dateString) return "";
    const date = new Date(dateString);
    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  };

  // Loading state (waiting for messages to load from API)
  const isLoading = loadedMessages === null;

  if (isLoading) {
    return (
      <AuthGuard
        requireDomain="serniacapital.com"
        message="Sernia AI assistant"
        icon={<Building className="w-16 h-16 text-muted-foreground" />}
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
      message="Sernia AI assistant"
      icon={<Building className="w-16 h-16 text-muted-foreground" />}
    >
      <Tabs
        defaultValue="chat"
        className="flex flex-col min-w-0 h-[calc(100dvh-52px)] bg-background"
      >
        {/* Header */}
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
                          conv.conversation_id === conversationId &&
                            "bg-muted"
                        )}
                      >
                        <button
                          onClick={() =>
                            loadConversation(conv.conversation_id)
                          }
                          className="flex-1 text-left min-w-0"
                        >
                          <div className="flex items-center justify-between">
                            <span className="text-sm truncate flex-1">
                              {conv.preview || "Empty conversation"}
                            </span>
                            {conv.has_pending && (
                              <Badge
                                variant="outline"
                                className="ml-2 text-xs"
                              >
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
                          onClick={(e) =>
                            deleteConversation(conv.conversation_id, e)
                          }
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </div>
                    ))
                  )}
                </div>
              </SheetContent>
            </Sheet>

            <TabsList>
              <TabsTrigger value="chat">Chat</TabsTrigger>
              <TabsTrigger value="instructions">
                System Instructions
              </TabsTrigger>
            </TabsList>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="gap-2"
            onClick={startNewConversation}
          >
            <Plus className="w-4 h-4" />
            New Chat
          </Button>
        </div>

        <TabsContent
          value="chat"
          className="flex-1 flex flex-col min-h-0 mt-0"
        >
          {/* ChatView — keyed by conversationId to force clean remount */}
          <ChatView
            key={conversationId}
            conversationId={conversationId}
            initialMessages={loadedMessages}
            initialPending={loadedPending}
            getToken={getToken}
          />
        </TabsContent>

        <TabsContent
          value="instructions"
          className="flex-1 flex flex-col min-h-0 mt-0"
        >
          <SystemInstructionsView getToken={getToken} />
        </TabsContent>
      </Tabs>
    </AuthGuard>
  );
}
