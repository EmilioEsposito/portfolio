import { motion } from "framer-motion";
import Link from "next/link";

import { SparklesIcon } from "./icons";

export const MultiAgentChatOverview = () => {
  return (
    <motion.div
      key="multi-agent-chat-overview"
      className="max-w-3xl mx-auto md:mt-20"
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.98 }}
      transition={{ delay: 0.5 }}
    >
      <div className="rounded-xl p-6 flex flex-col gap-8 leading-relaxed text-center max-w-xl">
        <div className="flex flex-row justify-center items-center">
          <div className="size-16 flex items-center rounded-full justify-center ring-2 ring-primary bg-primary/10">
            <SparklesIcon size={32} />
          </div>
        </div>
        
        <div className="flex flex-col gap-4">
          <h1 className="text-3xl font-bold">Multi-Agent AI Assistant</h1>
          <p className="text-muted-foreground">Ask me anything! I dynamically route your question to the right specialized AI agent. Here are the 2 agents I can route to:</p>
          <ul className="text-muted-foreground text-left list-disc list-inside space-y-2">
            <li><strong>Emilio Agent:</strong> Questions about Emilio's portfolio, skills, projects, and experience</li>
            <li><strong>Weather Agent:</strong> Current weather information for any location</li>
          </ul>
        </div>

        <div className="flex flex-col gap-3 text-sm text-left">
          <p className="font-semibold">Try the suggested prompts below, or ask your own question!</p>
    
        </div>

        <div className="border-t pt-6 text-sm text-muted-foreground">
          <p className="mb-3">
            <strong>Powered by:</strong>
          </p>
          <div className="flex flex-col gap-2">
            <p>
              <Link
                className="font-medium underline underline-offset-4"
                href="https://ai.pydantic.dev/graph/beta/"
                target="_blank"
              >
                PydanticAI Graph Beta API
              </Link>{" "}
              - Dynamic agent routing with graph-based workflows
            </p>
            <p>
              <Link
                className="font-medium underline underline-offset-4"
                href="https://ai.pydantic.dev"
                target="_blank"
              >
                PydanticAI
              </Link>{" "}
              - Type-safe agentic AI framework for Python
            </p>
            <p>
              <Link
                className="font-medium underline underline-offset-4"
                href="https://fastapi.tiangolo.com"
                target="_blank"
              >
                FastAPI
              </Link>{" "}
              - Modern Python web framework
            </p>
            <p>
              <Link
                className="font-medium underline underline-offset-4"
                href="https://sdk.vercel.ai/docs"
                target="_blank"
              >
                Vercel AI SDK
              </Link>{" "}
              - Streaming chat on Next.js
            </p>
          </div>
        </div>
      </div>
    </motion.div>
  );
};



