import { Link } from "react-router";

import { ContactEmail, LegalPage, LegalSection } from "~/components/sernia-public/legal-page";
import { serniaPageMeta } from "~/lib/sernia-public-seo";

// Written in kinds of information, uses and recipients, with no provider, field
// or retention period named, so a change in how the site works does not make it
// wrong. The text message section's wording is what carriers look for: keep it.

export const meta = () =>
  serniaPageMeta({
    title: "Privacy Policy",
    description: "How Sernia Capital collects, uses, shares and keeps personal information from this website.",
    path: "/privacy",
  });

export default function PrivacyPolicyPage() {
  return (
    <LegalPage title="Privacy Policy" updated="November 22, 2026">
      <p className="leading-7">
        This policy explains how Sernia Capital handles personal information we collect through this website and when
        you contact us about renting from us.
      </p>

      <LegalSection title="Information we collect">
        <ul>
          <li>Information you give us, such as your name, contact details and answers on our forms</li>
          <li>Our communications with you, and your inquiries and appointments</li>
          <li>
            Technical information that websites usually receive, such as your IP address and browser details, which may
            involve cookies or similar tools
          </li>
        </ul>
      </LegalSection>

      <LegalSection title="How we use it">
        <ul>
          <li>To respond to you, schedule and coordinate showings, and communicate with you about renting from us</li>
          <li>To operate, protect and improve our website and our business</li>
          <li>To meet our legal obligations</li>
        </ul>
      </LegalSection>

      <LegalSection title="How we share it">
        <p>We share personal information with:</p>
        <ul>
          <li>the people who work with us and need it to help you</li>
          <li>service providers that help us run our website and our business, for that purpose</li>
          <li>others when the law requires it, or to protect people's rights, property or safety</li>
        </ul>
        <p>We do not sell your personal information.</p>
      </LegalSection>

      <LegalSection title="Text messages">
        <p>
          If you agree to receive text messages, we may text you about showings and renting from us. Message frequency
          varies. Message and data rates may apply. Reply STOP to opt out or HELP for help. See our{" "}
          <Link to="/sms-terms" className="font-medium underline underline-offset-4">
            SMS Terms
          </Link>
          .
        </p>
        <p>
          We do not share mobile information with third parties or affiliates for marketing or promotional purposes.
          Text messaging opt-in data and consent are not shared with any third parties.
        </p>
      </LegalSection>

      <LegalSection title="How long we keep it">
        <p>
          We keep personal information for as long as we need it for the purposes above, or as the law requires.
        </p>
      </LegalSection>

      <LegalSection title="Other websites">
        <p>Websites we link to have their own privacy policies.</p>
      </LegalSection>

      <LegalSection title="Changes">
        <p>We may update this policy. The date at the top shows when it last changed.</p>
      </LegalSection>

      <LegalSection title="Contact">
        <p>
          Questions or requests about your personal information: <ContactEmail />.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
