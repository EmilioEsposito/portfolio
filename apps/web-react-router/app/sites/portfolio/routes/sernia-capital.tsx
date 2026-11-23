import { P } from "~/sites/portfolio/components/typography";
import { Link } from "react-router";
import { Badge } from "~/components/ui/badge";
import { Card, CardContent, CardHeader } from "~/components/ui/card";
import {
  Bot,
  AlertTriangle,
  Calendar,
  CalendarCheck,
  MessageSquare,
} from "lucide-react";
import { buildUrl, generateCanonicalLink, generatePageMeta } from "~/sites/portfolio/lib/seo";
export const meta = () =>
  generatePageMeta({
    title: "Sernia Capital systems",
    description:
      "AI agents and software Emilio Esposito builds for Sernia Capital: property operations, emergency SMS routing, rental inquiries, showing scheduling, and tenant communications.",
    path: "/sernia-capital",
  });
export const links = () => [generateCanonicalLink(buildUrl("/sernia-capital"))];
export default function SerniaCapital() {
  return (
    <main className="mx-auto max-w-4xl px-6 py-12">
      <Link to="/" className="text-sm underline underline-offset-4">
        Back to profile
      </Link>
      <h1 className="mt-8 text-4xl font-semibold tracking-tight">
        Sernia Capital systems
      </h1>{" "}
      {/* Sernia Capital */}
      <section
        aria-label="Operational systems"
        id="sernia-systems"
        className="mb-16 scroll-mt-6"
      >
        <P className="mt-4 text-muted-foreground">
          A few systems I've built to help run our properties at{" "}
          <a
            href="https://serniacapital.com/"
            className="text-foreground underline underline-offset-4 hover:text-foreground/80 transition-colors"
          >
            Sernia Capital
          </a>
          , from urgent tenant messages to rental inquiries and showings. All
          of them run in production.
        </P>

        <div className="mt-8 grid gap-4 md:grid-cols-2">
          <Card className="md:col-span-2">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold flex items-center gap-2 text-base">
                  <Bot className="h-4 w-4 text-cyan-500" />
                  SerniaAI Operations Agent
                </h2>
                <Badge variant="secondary" className="text-xs">
                  Production
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                A flexible AI agent for property managers, available through{" "}
                <a
                  href="https://www.quo.com/"
                  className="text-foreground underline underline-offset-4 hover:text-foreground/80 transition-colors"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Quo
                </a>{" "}
                SMS or our web app. It uses business communications and
                persistent memory to keep context, manage tasks, and help with
                follow-ups, with approval required for actions like sending
                external messages. It runs from chats, incoming events, or
                scheduled check-ins. Built with{" "}
                <a
                  href="https://ai.pydantic.dev/"
                  className="text-foreground underline underline-offset-4 hover:text-foreground/80 transition-colors"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  PydanticAI
                </a>
                .
              </p>
              <Link
                to="/systems/sernia-ai"
                className="mt-3 inline-flex text-sm underline underline-offset-4"
              >
                How the operations agent works
              </Link>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold flex items-center gap-2 text-base">
                  <AlertTriangle className="h-4 w-4 text-amber-500" />
                  Emergency SMS Routing Agent
                </h2>
                <Badge variant="secondary" className="text-xs">
                  Production
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                An event-driven AI specialist assesses incoming SMS for urgent
                issues. Deterministic webhook rules trigger the assessment;
                application code routes qualifying alerts through Twilio.
              </p>
              <Link
                to="/systems/emergency-routing"
                className="mt-3 inline-flex text-sm underline underline-offset-4"
              >
                How the routing agent works
              </Link>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold flex items-center gap-2 text-base">
                  <Calendar className="h-4 w-4 text-blue-500" />
                  Zillow Leads Agent
                </h2>
                <Badge variant="secondary" className="text-xs">
                  Production
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                A dedicated agent reads Zillow inquiries and reviewed rental listings,
                then drafts a reply, stays quiet, or asks a person for help.
                Every reply waits for human approval.
              </p>
              <Link
                to="/systems/leasing-leads"
                className="mt-3 inline-flex text-sm underline underline-offset-4"
              >
                How the leasing agent works
              </Link>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold flex items-center gap-2 text-base">
                  <MessageSquare className="h-4 w-4 text-green-500" />
                  Tenant Communications
                </h2>
                <Badge variant="secondary" className="text-xs">
                  Production
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Building-wide announcements by SMS, with role-based access so
                property managers can reach the right tenants.
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold flex items-center gap-2 text-base">
                  <CalendarCheck className="h-4 w-4 text-purple-500" />
                  Showing Scheduler
                </h2>
                <Badge variant="secondary" className="text-xs">
                  Production
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Prospects book a showing on{" "}
                <a
                  href="https://serniacapital.com/showings"
                  className="text-foreground underline underline-offset-4 hover:text-foreground/80 transition-colors"
                >
                  serniacapital.com
                </a>{" "}
                or by email. Open times come from the showing agents’ calendars
                and each listing’s showing rules. A booking puts the showing on
                the host’s calendar, confirms with the prospect, and gives
                residents notice by text.
              </p>
              <Link
                to="/systems/leasing-leads"
                className="mt-3 inline-flex text-sm underline underline-offset-4"
              >
                How showings are booked
              </Link>
            </CardContent>
          </Card>
        </div>
      </section>
    </main>
  );
}
