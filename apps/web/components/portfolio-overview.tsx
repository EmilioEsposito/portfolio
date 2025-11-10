import { motion } from "framer-motion";
import Link from "next/link";

import { SparklesIcon } from "./icons";

export const PortfolioOverview = () => {
  return (
    <motion.div
      key="portfolio-overview"
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
          <h1 className="text-3xl font-bold">Portfolio Assistant</h1>
          <p className="text-muted-foreground">
            Ask me anything about Emilio's skills, projects, and experience!
          </p>
        </div>

        <div className="flex flex-col gap-3 text-sm text-left">
          <p className="font-semibold">Try asking:</p>
          <ul className="list-disc list-inside space-y-2 text-muted-foreground">
            <li>"What technologies does Emilio work with?"</li>
            <li>"Tell me about his projects"</li>
            <li>"What's his experience with Python?"</li>
            <li>"Does he have experience with React?"</li>
          </ul>
        </div>

        <div className="border-t pt-6 text-sm text-muted-foreground">
          <p className="mb-3">
            <strong>Powered by:</strong>
          </p>
          <div className="flex flex-col gap-2">
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
