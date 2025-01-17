"use client";

import { Button } from "./ui/button";
import { GitIcon, LinkedInIcon } from "./icons";
import { Menu } from "lucide-react";
import Link from "next/link";
import { useSidebar } from "@/components/ui/sidebar";

export const Navbar = () => {
  const { toggleSidebar } = useSidebar();

  return (
    <div className="p-2 flex flex-row gap-2 justify-between items-center">
      <div className="flex gap-2 items-center">

        {/* Hamburger menu button that only shows on mobile */}
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleSidebar}
          className="h-8 w-8 md:hidden" // Show on mobile; hide on md and up
        >
          <Menu className="h-4 w-4" />
          <span className="sr-only">Toggle sidebar</span>
        </Button>

      </div>

      <div className="flex gap-2">


        {/* View source code button */}
        <Link href="https://github.com/EmilioEsposito/portfolio">
          <Button variant="outline">
            <GitIcon />
          </Button>
        </Link>
        <Link href="https://github.com/EmilioEsposito/portfolio">
          <Button variant="outline">
            <LinkedInIcon />
          </Button>
        </Link>

        {/* <Link href="https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2Fvercel-labs%2Fai-sdk-preview-python-streaming&env=OPENAI_API_KEY%2CVERCEL_FORCE_PYTHON_STREAMING&envDescription=API+keys+needed+for+application&envLink=https%3A%2F%2Fgithub.com%2Fvercel-labs%2Fai-sdk-preview-python-streaming%2Fblob%2Fmain%2F.env.example&teamSlug=vercel-labs">
          <Button>
            <VercelIcon />
            Deploy with Vercel
          </Button>
        </Link> */}
      </div>
    </div>
  );
};
