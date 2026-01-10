import type { Route } from "./+types/multi-agent-chat";
import { useState, useRef, useEffect } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { Button } from "~/components/ui/button";
import { Textarea } from "~/components/ui/textarea";
import { useScrollToBottom } from "~/hooks/use-scroll-to-bottom";
import { cn } from "~/lib/utils";
import { Markdown } from "~/components/markdown";
import { Weather } from "~/components/weather";
import { Zap, StopCircle } from "lucide-react";
import { SparklesIcon } from "~/components/icons";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Multi-Agent AI Assistant | Emilio Esposito" },
    {
      name: "description",
      content:
        "Chat with a multi-agent AI system that dynamically routes to specialized agents",
    },
  ];
}

const suggestedQuestions = [
  { title: "Tell me about Emilio", action: "Tell me about Emilio." },
  {
    title: "What's the weather in San Francisco?",
    action: "What's the weather in San Francisco?",
  },
  {
    title: "Tell me about Emilio's projects",
    action: "Tell me about Emilio's projects",
  },
  {
    title: "What's the weather in New York?",
    action: "What's the weather in New York?",
  },
];

// Tool invocation display component
const ToolInvocationDisplay = ({
  toolName,
  args,
  result,
}: {
  toolName: string;
  args?: any;
  result?: any;
}) => {
  const [isExpanded, setIsExpanded] = useState(false);

  const truncateJson = (obj: any, maxLength: number = 200): string => {
    const str = JSON.stringify(obj, null, 2);
    if (str.length <= maxLength) return str;
    return str.substring(0, maxLength) + "...";
  };

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-muted/20">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-4 py-2 text-left flex items-center justify-between hover:bg-muted/50 transition-colors"
      >
        <span className="text-sm font-medium flex items-center gap-2">
          <Zap className="w-4" />
          Used Tool:{" "}
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">
            {toolName}
          </code>
        </span>
        <span className="text-xs text-muted-foreground">
          {isExpanded ? "▼" : "▶"}
        </span>
      </button>
      {isExpanded && (
        <div className="px-4 py-3 bg-muted/30 border-t border-border space-y-3">
          {args !== undefined && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">
                Input:
              </div>
              <pre className="text-xs overflow-x-auto bg-background p-2 rounded border">
                {truncateJson(args)}
              </pre>
            </div>
          )}
          {result !== undefined && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">
                Output:
              </div>
              <pre className="text-xs overflow-x-auto bg-background p-2 rounded border max-h-64 overflow-y-auto">
                {truncateJson(result, 1000)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default function MultiAgentChatPage() {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const { messages, sendMessage, status, stop } = useChat({
    transport: new DefaultChatTransport({
      api: "/api/ai/multi-agent-chat",
      prepareSendMessagesRequest: ({ messages, body, trigger, id }) => {
        // Transform messages to UIMessage format with parts array
        const transformedMessages = messages.map((msg: any) => {
          // If message already has parts, use it as-is
          if (msg.parts) {
            return msg;
          }

          // Convert from { role, content } to { id, role, parts }
          return {
            id: msg.id || crypto.randomUUID(),
            role: msg.role,
            parts: [
              {
                type: "text",
                text: msg.content || "",
              },
            ],
          };
        });

        // Return body in the format expected by VercelAIAdapter
        return {
          body: {
            trigger,
            id: id || crypto.randomUUID(),
            messages: transformedMessages,
            ...body,
          },
        };
      },
    }),
  } as any);

  const [messagesContainerRef, messagesEndRef] =
    useScrollToBottom<HTMLDivElement>();

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
      sendMessage({
        role: "user",
        parts: [{ type: "text", text: input }],
      });
      setInput("");
    }
  };

  const handleSuggestedQuestion = (question: string) => {
    sendMessage({
      role: "user",
      parts: [{ type: "text", text: question }],
    });
  };

  // Process messages to extract tool invocations
  const processMessage = (message: any) => {
    let textContent = "";
    if (message.content && typeof message.content === "string") {
      textContent = message.content;
    } else if (message.parts && Array.isArray(message.parts)) {
      const textParts = message.parts.filter((part: any) => part.type === "text");
      textContent = textParts.map((p: any) => p.text).join("");
    }

    let toolInvocations: any[] = [];
    if (message.toolInvocations && Array.isArray(message.toolInvocations)) {
      toolInvocations = message.toolInvocations;
    } else if (message.parts && Array.isArray(message.parts)) {
      const allToolParts = message.parts.filter(
        (part: any) =>
          part.type?.startsWith("tool-") ||
          part.state === "output-available" ||
          part.state === "input-available"
      );

      toolInvocations = allToolParts.map((part: any) => ({
        toolCallId: part.toolCallId || part.id,
        toolName:
          part.toolName ||
          part.name ||
          (part.type?.startsWith("tool-")
            ? part.type.replace("tool-", "")
            : "unknown"),
        state:
          part.state === "output-available"
            ? "result"
            : part.state === "input-available"
              ? "call"
              : part.state,
        args: part.args || part.input,
        result: part.result || part.output,
      }));
    }

    return { textContent, toolInvocations };
  };

  return (
    <div className="flex flex-col min-w-0 h-[calc(100dvh-52px)] bg-background">
      {/* Messages */}
      <div
        ref={messagesContainerRef}
        className="flex flex-col min-w-0 gap-6 flex-1 overflow-y-scroll pt-4"
      >
        {messages.length === 0 ? (
          <div className="mx-auto w-full max-w-3xl px-4">
            <div className="flex flex-col items-center gap-4 py-4">
              <div className="flex flex-col items-center gap-2">
                <div className="size-16 flex items-center rounded-full justify-center ring-2 ring-primary bg-primary/10">
                  <SparklesIcon size={32} />
                </div>
                <h2 className="text-2xl font-bold">Multi-Agent AI Assistant</h2>
                <p className="text-sm text-muted-foreground text-center max-w-md">
                  Ask me anything! I dynamically route your question to the right
                  specialized AI agent.
                </p>
              </div>

              <div className="text-sm text-muted-foreground text-left space-y-2 mt-4 max-w-md">
                <p className="font-medium">Available agents:</p>
                <ul className="list-disc list-inside space-y-1">
                  <li>
                    <strong>Emilio Agent:</strong> Questions about Emilio's
                    portfolio, skills, projects, and experience
                  </li>
                  <li>
                    <strong>Weather Agent:</strong> Current weather information
                    for any location
                  </li>
                </ul>
              </div>

              <div className="text-xs text-muted-foreground text-center space-y-1 mt-4">
                <p className="font-medium">Powered by:</p>
                <div className="flex flex-col gap-0.5">
                  <a
                    href="https://ai.pydantic.dev/graph/beta/"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="hover:underline"
                  >
                    PydanticAI Graph Beta API
                  </a>
                  <span className="text-muted-foreground/60">
                    Dynamic agent routing with graph-based workflows
                  </span>
                  <a
                    href="https://fastapi.tiangolo.com/"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="hover:underline mt-1"
                  >
                    FastAPI
                  </a>
                  <span className="text-muted-foreground/60">
                    Modern Python web framework
                  </span>
                  <a
                    href="https://sdk.vercel.ai/"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="hover:underline mt-1"
                  >
                    Vercel AI SDK
                  </a>
                  <span className="text-muted-foreground/60">
                    Streaming chat on React Router
                  </span>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-3xl px-4 space-y-6">
            {messages.map((message, index) => {
              const { textContent, toolInvocations } = processMessage(message);

              return (
                <div
                  key={message.id || index}
                  className={cn(
                    "flex gap-3",
                    message.role === "user" ? "justify-end" : "justify-start"
                  )}
                >
                  {message.role === "assistant" && (
                    <div className="shrink-0">
                      <div className="size-8 flex items-center rounded-full justify-center ring-1 ring-border">
                        <SparklesIcon size={14} />
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
                        {/* Tool invocations */}
                        {toolInvocations.length > 0 && (
                          <div className="flex flex-col gap-2 w-full">
                            {toolInvocations.map((toolInvocation: any) => {
                              if (toolInvocation.state !== "result") return null;

                              // Special rendering for weather tool
                              if (
                                toolInvocation.toolName === "get_current_weather"
                              ) {
                                return (
                                  <div key={toolInvocation.toolCallId}>
                                    <Weather
                                      weatherAtLocation={toolInvocation.result}
                                    />
                                  </div>
                                );
                              }

                              return (
                                <ToolInvocationDisplay
                                  key={toolInvocation.toolCallId}
                                  toolName={toolInvocation.toolName}
                                  args={toolInvocation.args}
                                  result={toolInvocation.result}
                                />
                              );
                            })}
                          </div>
                        )}

                        {/* Text content */}
                        {textContent && (
                          <div className="bg-muted/50 rounded-2xl px-4 py-2.5 shadow-sm">
                            <div className="text-sm prose prose-sm dark:prose-invert max-w-none">
                              <Markdown>{textContent}</Markdown>
                            </div>
                          </div>
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
      <form className="flex mx-auto px-4 bg-background pb-4 md:pb-6 gap-2 w-full md:max-w-3xl">
        {messages.length === 0 ? (
          // Zero-state: Show suggestions AND input
          <div className="flex flex-col gap-4 w-full">
            <p className="text-sm text-muted-foreground text-center">
              Try the suggested prompts below, or ask your own question!
            </p>
            <div className="grid sm:grid-cols-2 gap-2">
              {suggestedQuestions.map((suggestion, index) => (
                <Button
                  key={index}
                  variant="ghost"
                  type="button"
                  onClick={() => handleSuggestedQuestion(suggestion.action)}
                  className="text-left border rounded-xl px-4 py-3.5 text-sm flex-1 gap-1 sm:flex-col w-full h-auto justify-start items-start"
                >
                  <span className="font-medium">{suggestion.title}</span>
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
                placeholder="Send a message..."
                className="min-h-[24px] max-h-[calc(75dvh)] overflow-hidden resize-none rounded-xl text-base bg-muted"
                rows={3}
                disabled={status === "submitted" || status === "streaming"}
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
                  disabled={!input.trim() || status === "submitted"}
                  className="h-11 w-11 shrink-0 rounded-xl"
                >
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="currentColor"
                    className="w-5 h-5"
                  >
                    <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
                  </svg>
                </Button>
              )}
            </div>
          </div>
        ) : (
          // Has messages: Show only input
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
              placeholder="Send a message..."
              className="min-h-[24px] max-h-[calc(75dvh)] overflow-hidden resize-none rounded-xl text-base bg-muted"
              rows={3}
              disabled={status === "submitted" || status === "streaming"}
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
                disabled={!input.trim() || status === "submitted"}
                className="h-11 w-11 shrink-0 rounded-xl"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                  className="w-5 h-5"
                >
                  <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
                </svg>
              </Button>
            )}
          </div>
        )}
      </form>
    </div>
  );
}
