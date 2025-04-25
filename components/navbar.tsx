"use client";

import { Button } from "./ui/button";
import { GitIcon, LinkedInIcon } from "./icons";
import { Menu, UserIcon } from "lucide-react";
import Link from "next/link";
import { useSidebar } from "@/components/ui/sidebar";
import {
  SignInButton,
  SignedIn,
  SignedOut,
  UserButton,
  useUser,
} from "@clerk/nextjs";

export const Navbar = () => {
  const { toggleSidebar } = useSidebar();
  const { user } = useUser();
  return (
    <div className="p-2 flex flex-row gap-2 justify-between items-center">
      <div className="flex gap-2 items-center">
        {/* Hamburger menu button that only shows on mobile */}
        <Button
          variant="ghost"
          onClick={toggleSidebar}
          className="h-10 w-10 p-2 md:hidden" // Increased touch target and added padding
        >
          <Menu className="h-6 w-6" /> {/* Increased icon size */}
          <span className="sr-only">Toggle sidebar</span>
        </Button>
      </div>

      <div className="flex gap-2">
        {/* View source code button */}
        <Link
          href="https://github.com/EmilioEsposito/portfolio"
        >
          <Button variant="outline">
            <GitIcon />
          </Button>
        </Link>
        <Link
          href="https://www.linkedin.com/in/emilioespositousa/"
        >
          <Button variant="outline">
            <LinkedInIcon />
          </Button>
        </Link>

        {/* Clerk Buttons */}
        <SignedOut>
          <SignInButton mode="modal">
            <Button variant="outline">
              <UserIcon />
              &nbsp;Login
            </Button>
          </SignInButton>
        </SignedOut>
        <SignedIn>
          <div className="flex items-center gap-2">
            {user?.firstName}
            <UserButton />
          </div>
        </SignedIn>

      </div>
    </div>
  );
};
