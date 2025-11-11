"use client";

import { useState } from "react";
import { PreviewMessage, ThinkingMessage } from "@/components/message";
import { MultimodalInput, type SuggestedAction } from "@/components/multimodal-input";
import { ChatEmilioOverview } from "@/components/chat-emilio-overview";
import { useScrollToBottom } from "@/hooks/use-scroll-to-bottom";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { toast } from "sonner";

const portfolioSuggestedActions: SuggestedAction[] = [
  {
    title: "Tell me about Emilio",
    action: "Tell me about Emilio.",
  },
  {
    title: "Tell me about his projects",
    action: "Tell me about Emilio's projects",
  },
  {
    title: "Where can I see some of Emilio's work?",
    action: "Where can I see some of Emilio's work?",
  },
  {
    title: "Is Emilio referenced in any public articles/interviews?",
    action: "Is Emilio referenced in any public articles/interviews?",
  },
];

export function ChatEmilio() {
  const chatId = "chat-emilio";
  
  // Manage input state manually (AI SDK v2+ removed it from useChat)
  const [input, setInput] = useState("");

  const {
    messages,
    setMessages,
    sendMessage,
    status,
    stop,
  } = useChat({
    transport: new DefaultChatTransport({
      api: "/api/ai/chat-emilio", 
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
    onError: (error: Error) => {
      console.error("Chat error:", error);
      toast.error(
        error.message || "An error occurred. Please try again.",
      );
    },
  } as any) as any;

  const [messagesContainerRef, messagesEndRef] =
    useScrollToBottom<HTMLDivElement>();

  // Handle form submission
  const handleSubmit = (e?: { preventDefault?: () => void }) => {
    if (!input.trim()) return;
    e?.preventDefault?.();
    sendMessage({ role: "user", content: input });
    setInput("");
  };

  // Compatibility wrapper for MultimodalInput
  const append = async (message: { role: string; content: string }) => {
    sendMessage({ role: "user", content: message.content });
    return null;
  };

  return (
    <div className="flex flex-col min-w-0 h-[calc(100dvh-52px)] bg-background">
      <div
        ref={messagesContainerRef}
        className="flex flex-col min-w-0 gap-6 flex-1 overflow-y-scroll pt-4"
      >
        {messages.length === 0 && <ChatEmilioOverview />}

        {messages
          .filter((message: any) => message.role !== "data")
          .map((message: any) => (
            <PreviewMessage
              key={message.id}
              chatId={chatId}
              message={message as any}
            />
          ))}

        {(status === 'submitted' || status === 'streaming') &&
          messages.length > 0 &&
          messages[messages.length - 1].role === "user" && <ThinkingMessage />}

        <div
          ref={messagesEndRef}
          className="shrink-0 min-w-[24px] min-h-[24px]"
        />
      </div>

      <form className="flex mx-auto px-4 bg-background pb-4 md:pb-6 gap-2 w-full md:max-w-3xl">
        <MultimodalInput
          chatId={chatId}
          input={input}
          setInput={setInput}
          handleSubmit={handleSubmit}
          status={status}
          stop={stop}
          messages={messages as any}
          setMessages={setMessages as any}
          append={append}
          suggestedActions={portfolioSuggestedActions}
        />
      </form>
    </div>
  );
}
