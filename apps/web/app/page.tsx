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
              Engineering & Data Science Leader | VP & Managing Partner at
              Sernia Capital
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
            Distinguished Engineer (Data/AI), with prior experience in various
            Director/Manager/IC roles in Data Science & Analytics at LegalZoom
            and Intuit.
          </P>
          <P>
            I also have over 13 years of experience in rental real estate
            investing & operations, and currently serve as VP & Managing Member
            for both Sernia Capital LLC and PANE Partners LLC, where we own and
            manage 40 apartment units.
          </P>
        </div>
      </div>

      {/* About Section */}
      <div className="mb-12">
        <H2>About</H2>
        <P className="mt-4">
          This platform has some technical projects that I do for fun/learning,
          but also hosts production apps used for Sernia Capital's in-house
          property managment operations. Sernia Capital is a residential real
          estate business that uses AI and automation to streamline various
          aspects of property mangagement, including tenant communications,
          maintenance requests, and general operations.
        </P>
      </div>

      {/* Key Areas */}
      <div className="grid gap-8 mb-12">
        <div className="space-y-2">
          <H2>Property Management Tools</H2>
          <P>
            Below are some of the production apps I've built and maintain for
            Sernia Capital. 
            
            Legend: 
            ✅ indicates the app is fully running in production.
            ⏸️ indicates the app is in development and not yet ready for production.
            🚧 indicates the app is in development and has some features working, but is not fully in production yet.
          </P>
        </div>
        <H3>
          {/* <Link href="/tenant-mass-message"> */}
          ✅ SMS Emergency Routing via Agentic AI
          {/* </Link> */}
        </H3>
        <P>
        Implemented in FastAPI here:{" "}
          <Link
            href="https://github.com/EmilioEsposito/portfolio/blob/main/api/src/open_phone/escalate.py"
            className="text-blue-500 hover:text-blue-600 hover:underline"
          >
            escalate.py
          </Link>
          <br />
          This monitors every incoming SMS text message sent to our business phone number 
          recieves, and escalates to property managers and owners as needed for 
          things that require URGENT action (e.g. water leaks, fire, police activity, etc.). 
          We use OpenPhone to host our business phone number, and they send a Webhook for every incoming message.
          Then, our AI instantly analyzes if the message is urgent, and if so, uses Twilio to kickoff a series of 
          calls/texts to the property managers and owners using a dedicated emergency number that 
          can bypass Do Not Disturb settings during off hours.
        </P>
        <H3>✅ Contact Syncing</H3>
        <P>
          A serverless cron job that syncs contact information from our source
          of truth Google Sheet to a Neon Postgres database and to our OpenPhone
          Voice/SMS Platform..
        </P>

        <H3>
          <Link href="/tenant-mass-messaging">✅ Tenant Mass Messaging</Link>
        </H3>
        <P>
          Preview app here:{" "}
          <Link
            href="/tenant-mass-messaging"
            className="text-blue-500 hover:text-blue-600 hover:underline"
          >
            Tenant Mass Messaging
          </Link>
          <br />
          Simple app to send SMS messages to all tenants in selected buildings.
          OpenPhone doesn't support sending to groups, so this app uses their
          API to achieve this. Messages are sent securely with password
          protection.
        </P>
        
        <H3>
          <Link href="/ai-email-responder">
          🚧 Rental Listing Email Auto-Replies via Agentic AI (Preview)
          </Link>
        </H3>
        <P>
          Preview app here:{" "}
          <Link
            href="/ai-email-responder"
            className="text-blue-500 hover:text-blue-600 hover:underline"
          >
            AI Email Responder Preview
          </Link>
          <br />
          This app auto-responds to inbound leasing inquiries from our Zillow
          ads. It will have context on all our properties via RAG, current
          rental listings (via Zillow API or scraping), as well as our listing
          agent's calendar availability & scheduling preferences. It will
          monitor our Gmail Workspace inbox for new inquiries, and will answer
          basic questions, will scan requestors Zillow profile and clarify any
          potential issues (e.g. if they have dogs but our listing is dog-free),
          and propose meeting times.
        </P>
        <H3>
          🚧 Fully Agentic Chatbot for Sernia Capital
        </H3>
        <P>
          The plan is to build a fully agentic chatbot for Sernia Capital that will have ability to 
          manage our tasks on Trello, read and send emails/push/sms, and more. On the backend, it will use tool/function
          calling to execute any of our internal services on the FastAPI server. It will even have access to 
          APScheduler, which will allow the chatbot to schedule of our internal services and initiate chats 
          with any property managers or owners (e.g. it can ping them to make sure a remodeling project is on track).
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
