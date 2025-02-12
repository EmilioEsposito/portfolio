import { H1, H2, H3, P } from "@/components/typography";
import Link from "next/link";
import Image from "next/image";

export default function Home() {
  return (
    <div className="container mx-auto px-4 py-8 max-w-4xl">
      {/* Hero Section */}
      <div className="space-y-4 mb-12">
        <div className="flex items-center gap-8">
          <div className="flex-1">
            <H1>Emilio Esposito</H1>
            <P className="text-lg text-muted-foreground">
              Engineering & Data Science Leader | VP & Managing Partner at Sernia Capital
            </P>
          </div>
          <div className="relative w-32 h-32 rounded-full overflow-hidden flex-shrink-0">
            <Image
              src="/images/me.png"
              alt="Emilio Esposito"
              fill
              className="object-cover"
              priority
            />
          </div>
        </div>
        <div className="mt-4 text-muted-foreground">
          <P>
            Distinguished Engineer (Data/AI), with prior experience in various Director/Manager/IC roles in Data Science & Analytics at LegalZoom and Intuit.
            </P>
            <P>
            I also have over 13 years of experience in rental real estate investing & operations, 
            and currently serve as VP & Managing Partner for both Sernia Capital LLC and PANE Partners 
            LLC, where we own and manage 40 apartment units. 
          </P>
        </div>
      </div>

      {/* About Section */}
      <div className="mb-12">
        <H2>About</H2>
        <P className="mt-4">
          This platform serves two purposes: showcasing some of my technical projects that I do for fun/learning, but also to host production solutions to
          Sernia Capital's property management operations. Sernia Capital is a residential 
          real estate business that uses AI and automation to streamline tenant communications,  
          maintenance requests, and general operations.
        </P>
      </div>

      {/* Key Areas */}
      <div className="grid gap-8 mb-12">
        <div className="space-y-2">
          <H2>Property Management Tools</H2>
          <P>
            Built on my experience in fintech and legal tech, these tools help manage our 
            growing portfolio of residential properties. Features include collaborative SMS (using OpenPhone API) and AI-powered 
            auto-replies to both SMS and Emails. 
          </P>
        </div>

        <H3>
          Contact Syncing 
        </H3>
        <P>
          A serverless cron job that syncs contact information from our source of truth Google Sheet to a Neon Postgres database and to our OpenPhone Voice/SMS Platform..
        </P>

        <H3>
          <Link href="/tenant-mass-message">
            Tenant Mass Messaging
          </Link>
        </H3>
        <P>
          Simple app to send SMS messages to all tenants in selected buildings. OpenPhone doesn't support sending to groups, so this app uses their API to achieve this. Messages are sent securely with password protection.
        </P>

        <H3>
          <Link href="/tenant-mass-message">
            Rental Listing Email Auto-Replies via Agentic AI (coming soon)
          </Link>
        </H3>
        <P>
          This app will auto-respond to inbound leasing inquiries from our Zillow ads. 
          It will have context on all our properties via RAG, current rental listings (via Zillow API or scraping), 
          as well as our listing agent's calendar availability & scheduling preferences. 
          It will monitor our Gmail Worspace inbox for new inquiries, and will answer basic questions,
          will scan requestors Zillow profile and clarify any potential issues (e.g. if they have dogs but our listing is dog-free),
          and propose meeting times. 
        </P>

        <H3>
          <Link href="/tenant-mass-message">
            SMS Emergency Routing via Agentic AI (coming soon)
          </Link>
        </H3>
        <P>
          This will monitor all incoming SMS mesages to Sernia 
          Capital's phone number, and will escalate them as needed if it detects the issue 
          is urgent (e.g. water leaks, fire, police activity, etc.).
          It will kick off a call to a dedicated emergency number that calls and texts all 
          owners/property managers and can bypass Do Not Disturb settings during off hours. 
        </P>

      </div>

      {/* Navigation Links */}
      {/* <div className="mt-12 flex gap-4">
        <Link 
          href="/tools" 
          className="text-primary hover:underline font-medium"
        >
          Explore Tools
        </Link>
        <Link 
          href="/projects" 
          className="text-primary hover:underline font-medium"
        >
          View Projects
        </Link>
      </div> */}
    </div>
  );
}
