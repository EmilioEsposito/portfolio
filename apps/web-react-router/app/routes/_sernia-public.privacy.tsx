import { Link } from "react-router";

import { ContactEmail, LegalPage, LegalSection } from "~/components/sernia-public/legal-page";
import { serniaPageMeta } from "~/lib/sernia-public-seo";

export const meta = () =>
  serniaPageMeta({
    title: "Privacy Policy",
    description:
      "What Sernia Capital collects when you book a showing of a rental unit, how it is used, who sees it and how long it is kept.",
    path: "/privacy",
  });

export default function PrivacyPolicyPage() {
  return (
    <LegalPage title="Privacy Policy" updated="November 15, 2026">
      <p className="leading-7">
        Sernia Capital rents apartments. This policy covers the information we collect when you book a showing of
        one of our units on this site, how we use it and how long we keep it.
      </p>

      <LegalSection title="What we collect">
        <p>The booking form asks for:</p>
        <ul>
          <li>your name</li>
          <li>your mobile number</li>
          <li>your email address</li>
          <li>
            two confirmations: that your lease can start in the dates shown for the unit, and that you can meet the
            pet rule shown on the form
          </li>
        </ul>
        <p>
          With your booking we keep the showing you chose, your agreement to receive texts, and the wording of each
          confirmation with the time you gave it.
        </p>
      </LegalSection>

      <LegalSection title="How we use it">
        <ul>
          <li>to book your showing and send you a confirmation and a calendar invitation</li>
          <li>to remind you before the showing and coordinate it, for example if the time changes</li>
          <li>to follow up about your rental inquiry, including how to apply</li>
        </ul>
      </LegalSection>

      <LegalSection title="Who we share it with">
        <p>
          Only the people who need your details to run your showing see them, such as the member of our team or the
          agent who meets you at the unit. Service providers that send our texts and calendar invitations and host
          this site process your details for us and may use them only to provide those services.
        </p>
        <p>We do not sell your information.</p>
      </LegalSection>

      <LegalSection title="Text messages">
        <p>
          We text you only if you agree on the booking form. Message frequency varies. Message and data rates may
          apply. Reply STOP to opt out or HELP for help. Our{" "}
          <Link to="/sms-terms" className="font-medium underline underline-offset-4">
            SMS Terms
          </Link>{" "}
          have the details.
        </p>
        <p>
          We do not share mobile information with third parties or affiliates for marketing or promotional purposes.
          Text messaging opt-in data and consent are not shared with any third parties.
        </p>
      </LegalSection>

      <LegalSection title="How long we keep it">
        <p>
          We delete your details 12 months after your showing, unless you apply to rent from us. If you apply, we keep
          them with your application.
        </p>
      </LegalSection>

      <LegalSection title="Contact">
        <p>
          Questions about this policy or your information: <ContactEmail />.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
