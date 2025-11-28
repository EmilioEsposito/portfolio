import type { Route } from "./+types/chat-weather";
import { useState, useRef, useEffect } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { Button } from "~/components/ui/button";
import { Textarea } from "~/components/ui/textarea";
import { useScrollToBottom } from "~/hooks/use-scroll-to-bottom";
import { cn } from "~/lib/utils";
import { Markdown } from "~/components/markdown";
import { Weather } from "~/components/weather";
import { Cloud, Zap, StopCircle } from "lucide-react";
import { MessageIcon } from "~/components/icons";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "AI Weather Agent | Emilio Esposito" },
    {
      name: "description",
      content: "Get current weather information from an AI-powered weather agent",
    },
  ];
}

const suggestedQuestions = [
  {
    title: "What is the weather in San Francisco?",
    action: "What is the weather in San Francisco?",
  },
  {
    title: "What is the weather in New York?",
    action: "What is the weather in New York?",
  },
  {
    title: "What is the weather in Pittsburgh?",
    action: "What is the weather in Pittsburgh?",
  },
  {
    title: "What is the weather in Rome Italy?",
    action: "What is the weather in Rome Italy?",
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
          <Zap className="w-4 w-4" />
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

// Python Logo Icon
const LogoPython = ({ size = 32 }: { size?: number }) => (
  <svg
    height={size}
    width={size}
    viewBox="0 0 256 255"
    xmlns="http://www.w3.org/2000/svg"
    preserveAspectRatio="xMidYMid"
  >
    <defs>
      <linearGradient x1="12.959%" y1="12.039%" x2="79.639%" y2="78.201%" id="a">
        <stop stopColor="#387EB8" offset="0%" />
        <stop stopColor="#366994" offset="100%" />
      </linearGradient>
      <linearGradient x1="19.128%" y1="20.579%" x2="90.742%" y2="88.429%" id="b">
        <stop stopColor="#FFE052" offset="0%" />
        <stop stopColor="#FFC331" offset="100%" />
      </linearGradient>
    </defs>
    <path
      d="M126.916.072c-64.832 0-60.784 28.115-60.784 28.115l.072 29.128h61.868v8.745H41.631S.145 61.355.145 126.77c0 65.417 36.21 63.097 36.21 63.097h21.61v-30.356s-1.165-36.21 35.632-36.21h61.362s34.475.557 34.475-33.319V33.97S194.67.072 126.916.072zM92.802 19.66a11.12 11.12 0 0 1 11.13 11.13 11.12 11.12 0 0 1-11.13 11.13 11.12 11.12 0 0 1-11.13-11.13 11.12 11.12 0 0 1 11.13-11.13z"
      fill="url(#a)"
    />
    <path
      d="M128.757 254.126c64.832 0 60.784-28.115 60.784-28.115l-.072-29.127H127.6v-8.745h86.441s41.486 4.705 41.486-60.712c0-65.416-36.21-63.096-36.21-63.096h-21.61v30.355s1.165 36.21-35.632 36.21h-61.362s-34.475-.557-34.475 33.32v56.013s-5.235 33.897 62.518 33.897zm34.114-19.586a11.12 11.12 0 0 1-11.13-11.13 11.12 11.12 0 0 1 11.13-11.131 11.12 11.12 0 0 1 11.13 11.13 11.12 11.12 0 0 1-11.13 11.13z"
      fill="url(#b)"
    />
  </svg>
);

export default function ChatWeatherPage() {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const { messages, sendMessage, status, stop } = useChat({
    transport: new DefaultChatTransport({
      api: "/api/ai/chat-weather",
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
      sendMessage({ role: "user", content: input });
      setInput("");
    }
  };

  const handleSuggestedQuestion = (question: string) => {
    sendMessage({ role: "user", content: question });
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
              <div className="flex flex-row justify-center gap-4 items-center">
                <LogoPython size={32} />
                <span className="text-2xl">+</span>
                <MessageIcon size={32} />
              </div>
              <h2 className="text-2xl font-bold">AI Weather Agent</h2>
              <p className="text-sm text-muted-foreground text-center max-w-md">
                Right now, this is just a simple weather chatbot using OpenAI's
                model. I am also working on a more complex chatbot for Sernia
                Capital that will have full agentic ability to manage our tasks
                on Trello, schedule emails/push/sms, and more.
              </p>

              <div className="text-xs text-muted-foreground text-center space-y-2 mt-4 max-w-md">
                <p>
                  Technical details: This chat interface is using an{" "}
                  <a
                    className="font-medium underline underline-offset-4"
                    href="https://github.com/vercel-labs/ai-sdk-preview-python-streaming"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    open source
                  </a>{" "}
                  template that demonstrates the usage of{" "}
                  <a
                    className="font-medium underline underline-offset-4"
                    href="https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol#data-stream-protocol"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Data Stream Protocol
                  </a>{" "}
                  to stream chat completions from a Python function (
                  <a
                    className="font-medium underline underline-offset-4"
                    href="https://fastapi.tiangolo.com"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    FastAPI
                  </a>
                  ) along with the{" "}
                  <code className="rounded-md bg-muted px-1 py-0.5">useChat</code>{" "}
                  hook on the client to create a seamless chat experience.
                </p>
                <p>
                  You can learn more about the AI SDK by visiting the{" "}
                  <a
                    className="font-medium underline underline-offset-4"
                    href="https://sdk.vercel.ai/docs"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    docs
                  </a>
                  .
                </p>
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
                    <div className="flex-shrink-0">
                      <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center">
                        <Cloud className="w-5 h-5 text-primary-foreground" />
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
                    <div className="flex-shrink-0">
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
