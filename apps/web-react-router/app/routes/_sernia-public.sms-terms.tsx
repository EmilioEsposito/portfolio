import { Link } from "react-router";

import { ContactEmail, LegalPage, LegalSection } from "~/components/sernia-public/legal-page";
import { serniaPageMeta } from "~/lib/sernia-public-seo";

export const meta = () =>
  serniaPageMeta({
    title: "SMS Terms",
    description:
      "Terms for Sernia Capital's text messages about showings and rental inquiries: what is sent, how often, rates, STOP and HELP.",
    path: "/sms-terms",
  });

export default function SmsTermsPage() {
  return (
    <LegalPage title="SMS Terms" updated="November 15, 2026">
      <p className="leading-7">
        These terms cover the text messages Sernia Capital sends about showings of our rental units.
      </p>

      <LegalSection title="What we send">
        <p>
          When you book a showing and tick “Text me about this showing and my rental inquiry,” Sernia Capital texts
          you from our team number to confirm, remind and coordinate your showing, and about your rental inquiry.
          That usually means a confirmation when you book, a reminder the evening before, a message if anything
          changes, and a follow-up after the showing about how to apply.
        </p>
        <p>Message frequency varies. Message and data rates may apply.</p>
      </LegalSection>

      <LegalSection title="Stopping texts and getting help">
        <p>
          Reply STOP to any of our texts to stop receiving them. Reply HELP for help, or email <ContactEmail />.
        </p>
      </LegalSection>

      <LegalSection title="Booking without texts">
        <p>If you would rather not get texts, call us instead. The booking page shows our number.</p>
      </LegalSection>

      <LegalSection title="Carriers">
        <p>Carriers are not liable for delayed or undelivered messages.</p>
      </LegalSection>

      <LegalSection title="Privacy">
        <p>
          We do not share mobile information with third parties or affiliates for marketing or promotional purposes.
          Our{" "}
          <Link to="/privacy" className="font-medium underline underline-offset-4">
            Privacy Policy
          </Link>{" "}
          explains what we collect, how we use it and how long we keep it.
        </p>
      </LegalSection>

      <LegalSection title="Contact">
        <p>
          <ContactEmail />
        </p>
      </LegalSection>
    </LegalPage>
  );
}
