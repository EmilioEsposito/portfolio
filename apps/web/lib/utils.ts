import type { UIMessage } from "ai";
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function sanitizeUIMessages(messages: Array<UIMessage>): Array<UIMessage> {
  return messages
    .map((message) => {
      if (message.role !== "assistant") return message;

      const filteredParts = message.parts.filter((part) => {
        if (part.type === "text") {
          return part.text.trim().length > 0;
        }

        if (part.type === "dynamic-tool" || part.type.startsWith("tool-")) {
          return "state" in part && part.state === "output-available";
        }

        return true;
      });

      return {
        ...message,
        parts: filteredParts,
      };
    })
    .filter((message) => message.parts.length > 0);
}
