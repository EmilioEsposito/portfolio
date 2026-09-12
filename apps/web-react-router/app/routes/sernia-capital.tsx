import { P } from "~/components/typography";
import { Link } from "react-router";
import { Badge } from "~/components/ui/badge";
import { Card, CardContent, CardHeader } from "~/components/ui/card";
import {
  Bot,
  AlertTriangle,
  Calendar,
  MessageSquare,
  Mail,
  ExternalLink,
} from "lucide-react";
import { buildUrl, generateCanonicalLink, generatePageMeta } from "~/lib/seo";
export const meta = () =>
  generatePageMeta({
    title: "Sernia Capital systems",
    description:
      "AI agents and software Emilio Esposito builds for Sernia Capital: property operations, emergency SMS routing, leasing follow-ups, and tenant communications.",
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
          A few systems I've built to help run our properties, from urgent
          tenant messages to leasing follow-ups. Most are part of our daily
          operations; others are still in development.
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
                  Leasing Lead Management Agent
                </h2>
                <Badge variant="secondary" className="text-xs">
                  Production
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                An event-driven AI specialist interprets leasing email threads.
                Scheduled jobs trigger review; structured AI decisions drive
                contact updates, calendar events, and missing-reply alerts.
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
              <Link
                to="/message-tenants"
                className="mt-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                Open app <ExternalLink className="h-3 w-3" />
              </Link>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold flex items-center gap-2 text-base">
                  <Mail className="h-4 w-4 text-purple-500" />
                  AI Leasing Auto-Replies
                </h2>
                <Badge variant="outline" className="text-xs">
                  In Development
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Drafting and testing automated replies to leasing inquiries
                using property details, listings, and agent availability, with
                applicant screening before suggesting a showing.
              </p>
              <Link
                to="/ai-email-responder"
                className="mt-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                Preview <ExternalLink className="h-3 w-3" />
              </Link>
            </CardContent>
          </Card>
        </div>
      </section>
    </main>
  );
}
