"use client";

import { PreviewMessage, ThinkingMessage } from "@/components/message";
import { MultimodalInput } from "@/components/multimodal-input";
import { PortfolioOverview } from "@/components/portfolio-overview";
import { useScrollToBottom } from "@/hooks/use-scroll-to-bottom";
import { useChat } from "@ai-sdk/react";
import { toast } from "sonner";

export function PortfolioChat() {
  const chatId = "portfolio-chat";

  const {
    messages,
    setMessages,
    handleSubmit,
    input,
    setInput,
    append,
    status,
    stop,
  } = useChat({
    api: "/api/ai/chat",
    maxSteps: 3,
    onError: (error) => {
      console.error("Chat error:", error);
      toast.error(
        error.message || "An error occurred. Please try again.",
      );
    },
  });

  const [messagesContainerRef, messagesEndRef] =
    useScrollToBottom<HTMLDivElement>();

  return (
    <div className="flex flex-col min-w-0 h-[calc(100dvh-52px)] bg-background">
      <div
        ref={messagesContainerRef}
        className="flex flex-col min-w-0 gap-6 flex-1 overflow-y-scroll pt-4"
      >
        {messages.length === 0 && <PortfolioOverview />}

        {messages
          .filter((message) => message.role !== "data")
          .map((message) => (
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
        />
      </form>
    </div>
  );
}
