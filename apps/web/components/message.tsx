"use client";

import { useState } from "react";
import type { UIMessage } from "ai";
import { motion } from "framer-motion";

import { SparklesIcon } from "./icons";
import { Markdown } from "./markdown";
import { PreviewAttachment } from "./preview-attachment";
import { cn } from "@/lib/utils";
import { Weather } from "./weather";

export const PreviewMessage = ({
  message,
}: {
  chatId: string;
  message: any;
}) => {
  // Extract tool invocations from message
  // According to AI SDK v2 stream protocol (https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol):
  // Tool invocations come through as parts in message.parts
  // The AI SDK's useChat hook parses stream events and creates these parts
  let toolInvocations: any[] = [];
  
  // Debug: Log message structure to understand what we're working with
  if (message.role === "assistant" && (message.parts || message.toolInvocations)) {
    console.log("Message structure:", {
      role: message.role,
      hasParts: !!message.parts,
      partsLength: message.parts?.length,
      partsTypes: message.parts?.map((p: any) => p.type),
      hasToolInvocations: !!message.toolInvocations,
      toolInvocationsLength: message.toolInvocations?.length,
      fullMessage: message,
    });
  }
  
  // Check direct toolInvocations array (legacy format)
  if (message.toolInvocations && Array.isArray(message.toolInvocations)) {
    toolInvocations = message.toolInvocations;
  }
  // Check parts array for tool parts (AI SDK v2 format)
  else if (message.parts && Array.isArray(message.parts)) {
    // Extract tool invocations from parts
    // Filter for tool-related parts
    const allToolParts = message.parts.filter((part: any) => 
      part.type?.startsWith("tool-") || 
      part.state === "output-available" ||
      part.state === "input-available"
    );
    
    if (allToolParts.length > 0) {
      console.log("Found tool parts:", allToolParts);
    }
    
    // Convert tool parts to tool invocations
    toolInvocations = allToolParts.map((part: any) => ({
      toolCallId: part.toolCallId || part.id,
      toolName: part.toolName || part.name,
      state: part.state,
      args: part.args || part.input,
      result: part.result || part.output,
      // Keep original part for normalization to extract toolName from type if needed
      _originalPart: part,
    }));
  }
  // Check experimental_toolCalls (alternative location)
  else if (message.experimental_toolCalls && Array.isArray(message.experimental_toolCalls)) {
    toolInvocations = message.experimental_toolCalls;
  }
  
  if (toolInvocations.length > 0) {
    console.log("Extracted tool invocations:", toolInvocations);
  }
  
  // Track toolNames by toolCallId for parts that might be missing toolName
  const toolNameMap = new Map<string, string>();
  
  // First pass: collect toolNames from parts that have them
  toolInvocations.forEach((invocation: any) => {
    const toolCallId = invocation.toolCallId || invocation.id;
    if (!toolCallId) return;
    
    // Extract toolName from various sources
    let toolName = invocation.toolName || invocation.name;
    
    // If missing, try to extract from type field (format: "tool-{toolName}")
    if (!toolName && invocation._originalPart?.type?.startsWith("tool-")) {
      toolName = invocation._originalPart.type.replace("tool-", "");
    }
    
    if (toolName) {
      toolNameMap.set(toolCallId, toolName);
    }
  });
  
  // Transform tool invocations to expected format
  const normalizedToolInvocations = toolInvocations.map((invocation: any) => {
    const toolCallId = invocation.toolCallId || invocation.id;
    
    // Get toolName - use existing or lookup from map
    let toolName = invocation.toolName || invocation.name;
    if (!toolName && invocation._originalPart?.type?.startsWith("tool-")) {
      toolName = invocation._originalPart.type.replace("tool-", "");
    }
    if (!toolName && toolCallId) {
      toolName = toolNameMap.get(toolCallId);
    }
    
    // Normalize state
    let state = invocation.state;
    if (state === "output-available") {
      state = "result";
    } else if (state === "input-available") {
      state = "call";
    } else if (!state) {
      state = invocation.result !== undefined ? "result" : "call";
    }
    
    // Return normalized format
    return {
      toolCallId,
      toolName: toolName || "unknown",
      state,
      result: invocation.result || invocation.output,
      args: invocation.args || invocation.input,
    };
  });

  return (
    <motion.div
      className="w-full mx-auto max-w-3xl px-4 group/message"
      initial={{ y: 5, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      data-role={message.role}
    >
      <div
        className={cn(
          "group-data-[role=user]/message:bg-primary group-data-[role=user]/message:text-primary-foreground flex gap-4 group-data-[role=user]/message:px-3 w-full group-data-[role=user]/message:w-fit group-data-[role=user]/message:ml-auto group-data-[role=user]/message:max-w-2xl group-data-[role=user]/message:py-2 rounded-xl",
        )}
      >
        {message.role === "assistant" && (
          <div className="size-8 flex items-center rounded-full justify-center ring-1 shrink-0 ring-border">
            <SparklesIcon size={14} />
          </div>
        )}

        <div className="flex flex-col gap-2 w-full">
          {/* Render tool invocations first, before streaming text */}
          {normalizedToolInvocations && normalizedToolInvocations.length > 0 && (
            <div className="flex flex-col gap-4">
              {normalizedToolInvocations.map((toolInvocation: any) => {
                const { toolName, toolCallId, state, result, args } = toolInvocation;

                // Only render completed tool invocations (state === "result")
                if (state !== "result") {
                  return null;
                }

                // Custom renderers for specific tools
                if (toolName === "get_current_weather") {
                  return (
                    <div key={toolCallId}>
                      <Weather weatherAtLocation={result} />
                    </div>
                  );
                }

                // For other tools, render as collapsible section (optional - can be disabled)
                // Only render if we have a result
                if (result !== undefined) {
                  return (
                    <ToolInvocationDisplay
                      key={toolCallId}
                      toolName={toolName || "unknown"}
                      args={args}
                      result={result}
                    />
                  );
                }

                return null;
              })}
            </div>
          )}

          {/* Render text content below tool invocations */}
          {(() => {
            // Prefer parts over content when both exist (parts is more structured)
            // Filter out tool-related parts - they're rendered separately above
            const textParts = message.parts?.filter((part: any) => 
              part.type === "text" && 
              !part.type?.startsWith("tool-") &&
              part.state !== "output-available" &&
              part.state !== "input-available"
            ) || [];
            const textContent = textParts.length > 0
              ? textParts.map((part: any) => part.text).join("")
              : message.content;
            
            return textContent && (
              <div className="flex flex-col gap-4">
                <Markdown>
                  {textContent}
                </Markdown>
              </div>
            );
          })()}

          {message.experimental_attachments && (
            <div className="flex flex-row gap-2">
              {message.experimental_attachments.map((attachment: any) => (
                <PreviewAttachment
                  key={attachment.url}
                  attachment={attachment}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
};

// Collapsible tool invocation display component
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
    <div className="border border-border rounded-lg overflow-hidden">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-4 py-2 text-left flex items-center justify-between hover:bg-muted/50 transition-colors"
      >
        <span className="text-sm font-medium">
          Used Tool: <code className="text-xs">{toolName}</code>
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
              <pre className="text-xs overflow-x-auto bg-background p-2 rounded border">
                {truncateJson(result)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export const ThinkingMessage = () => {
  const role = "assistant";

  return (
    <motion.div
      className="w-full mx-auto max-w-3xl px-4 group/message "
      initial={{ y: 5, opacity: 0 }}
      animate={{ y: 0, opacity: 1, transition: { delay: 1 } }}
      data-role={role}
    >
      <div
        className={cn(
          "flex gap-4 group-data-[role=user]/message:px-3 w-full group-data-[role=user]/message:w-fit group-data-[role=user]/message:ml-auto group-data-[role=user]/message:max-w-2xl group-data-[role=user]/message:py-2 rounded-xl",
          {
            "group-data-[role=user]/message:bg-muted": true,
          },
        )}
      >
        <div className="size-8 flex items-center rounded-full justify-center ring-1 shrink-0 ring-border">
          <SparklesIcon size={14} />
        </div>

        <div className="flex flex-col gap-2 w-full">
          <div className="flex flex-col gap-4 text-muted-foreground">
            Thinking...
          </div>
        </div>
      </div>
    </motion.div>
  );
};
