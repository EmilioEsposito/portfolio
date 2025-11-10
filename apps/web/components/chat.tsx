"use client";

import { PreviewMessage, ThinkingMessage } from "@/components/message";
import { MultimodalInput } from "@/components/multimodal-input";
import { Overview } from "@/components/overview";
import { useScrollToBottom } from "@/hooks/use-scroll-to-bottom";
import { DefaultChatTransport } from "ai";
import { useChat } from "@ai-sdk/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

export function Chat() {
  const chatId = "001";

  const transport = useMemo(
    () => new DefaultChatTransport({ api: "/api/pydantic-ai/portfolio" }),
    [],
  );

  const { messages, setMessages, sendMessage, stop, status, error } = useChat({
    id: chatId,
    transport,
    experimental_throttle: 32,
  });

  const [input, setInput] = useState("");

  useEffect(() => {
    if (!error) return;

    if (error.message.includes("Too many requests")) {
      toast.error(
        "You are sending too many messages. Please try again later.",
      );
      return;
    }

    toast.error(error.message);
  }, [error]);

  const submitMessage = useCallback(async () => {
    const trimmed = input.trim();
    if (!trimmed) return;

    setInput("");

    try {
      await sendMessage({ text: trimmed });
    } catch (sendError) {
      setInput(trimmed);
      const message =
        sendError instanceof Error
          ? sendError.message
          : "Something went wrong while sending your message.";
      toast.error(message);
    }
  }, [input, sendMessage]);

  const [messagesContainerRef, messagesEndRef] =
    useScrollToBottom<HTMLDivElement>();

  return (
    <div className="flex flex-col min-w-0 h-[calc(100dvh-52px)] bg-background">
      <div
        ref={messagesContainerRef}
        className="flex flex-col min-w-0 gap-6 flex-1 overflow-y-scroll pt-4"
      >
        {messages.length === 0 && <Overview />}

        {messages.map((message) => (
          <PreviewMessage
            key={message.id}
            chatId={chatId}
            message={message}
          />
        ))}

        {(status === "submitting" || status === "streaming") &&
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
          onSubmit={submitMessage}
          status={status}
          stop={stop}
          messages={messages}
          setMessages={setMessages}
          sendMessage={sendMessage}
        />
      </form>
    </div>
  );
}
