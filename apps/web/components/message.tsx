"use client";

import {
  getToolOrDynamicToolName,
  isToolOrDynamicToolUIPart,
  type UIMessage,
} from "ai";
import { motion } from "framer-motion";

import { SparklesIcon } from "./icons";
import { Markdown } from "./markdown";
import { PreviewAttachment } from "./preview-attachment";
import { cn } from "@/lib/utils";
import {
  ExecutionWindowCard,
  PortfolioBlueprintCard,
} from "./portfolio-blueprint";
import type {
  ExecutionWindowResult,
  OpportunityBlueprintResult,
} from "@/types/pydantic-ai";
import { Weather } from "./weather";

export const PreviewMessage = ({
  message,
}: {
  chatId: string;
  message: UIMessage;
}) => {
  const textContent = getTextFromMessage(message);
  const toolOutputs = getToolOutputs(message);

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
          {textContent && (
            <div className="flex flex-col gap-4">
              <Markdown>{textContent}</Markdown>
            </div>
          )}

          {toolOutputs.length > 0 && (
            <div className="flex flex-col gap-4">
              {toolOutputs.map((tool) => (
                <div key={tool.id} className="space-y-3">
                  {renderToolResult(tool)}
                </div>
              ))}
            </div>
          )}

          {message.experimental_attachments && (
            <div className="flex flex-row gap-2">
              {message.experimental_attachments.map((attachment) => (
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

function getTextFromMessage(message: UIMessage): string {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n\n")
    .trim();
}

type ToolResult = {
  id: string;
  name: string;
  output: unknown;
};

function getToolOutputs(message: UIMessage): ToolResult[] {
  if (message.role !== "assistant") return [];

  return message.parts
    .filter(
      (part): part is Extract<UIMessage["parts"][number], { state: string }> =>
        isToolOrDynamicToolUIPart(part) && part.state === "output-available",
    )
    .map((part) => ({
      id: `${message.id}-${part.toolCallId ?? getToolOrDynamicToolName(part)}`,
      name: getToolOrDynamicToolName(part),
      output: part.output,
    }));
}

function renderToolResult(tool: ToolResult) {
  if (tool.name === "get_current_weather") {
    return <Weather weatherAtLocation={tool.output} />;
  }

  if (tool.name === "sketch_blueprint" && isBlueprintResult(tool.output)) {
    return <PortfolioBlueprintCard blueprint={tool.output} />;
  }

  if (tool.name === "estimate_delivery" && isExecutionResult(tool.output)) {
    return <ExecutionWindowCard execution={tool.output} />;
  }

  return (
    <pre className="text-xs bg-muted rounded-md p-3 overflow-x-auto">
      {JSON.stringify(tool.output, null, 2)}
    </pre>
  );
}

function isBlueprintResult(value: unknown): value is OpportunityBlueprintResult {
  if (!value || typeof value !== "object") return false;

  const maybe = value as Record<string, unknown>;
  return (
    typeof maybe.working_title === "string" &&
    typeof maybe.north_star === "string" &&
    typeof maybe.elevator_pitch === "string" &&
    Array.isArray(maybe.signature_experiences) &&
    typeof maybe.execution_window === "object" &&
    maybe.execution_window !== null
  );
}

function isExecutionResult(value: unknown): value is ExecutionWindowResult {
  if (!value || typeof value !== "object") return false;

  const maybe = value as Record<string, unknown>;
  return (
    typeof maybe.sprint_weeks === "number" &&
    typeof maybe.cadence === "string" &&
    typeof maybe.estimated_cost === "number"
  );
}
