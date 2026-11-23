import LeasingArchitecture from "~/components/leasing-architecture";

// How the Zillow leads system works, defined once: the architecture diagram,
// who is told what about a showing, and what is left to people. The
// portfolio's write-up (/systems/leasing-leads) and the team's overview
// (/team/leads) both render it under their own introduction and links.

// Who hears about a showing, and when. One row per moment, one column per person.
const told: { when: string; lead: string; host: string; residents: string }[] = [
  {
    when: "A showing is booked",
    lead: "A confirmation text with a link to cancel, then a calendar invite. Booked by email: the confirming email only, until they add their details.",
    host: "An event on their calendar, listing who is coming.",
    residents: "One group text, in the daytime. Nothing more when another lead joins.",
  },
  {
    when: "The evening before",
    lead: "A reminder text. Booked by email without details: a reminder email in the same thread.",
    host: "",
    residents: "A reminder text listing the next day’s showings.",
  },
  {
    when: "The morning of",
    lead: "",
    host: "One text with the day’s showings, in order.",
    residents: "",
  },
  {
    when: "A change within two hours of the start",
    lead: "",
    host: "A text saying who joined or cancelled.",
    residents: "",
  },
  {
    when: "A showing is cancelled or moved",
    lead: "A text confirming the cancellation, and the invite is withdrawn. A move by email is confirmed in the reply.",
    host: "The event is updated, or removed when no one is left.",
    residents: "A text saying the showing is off, if they had been told about it.",
  },
  {
    when: "Two hours after the start",
    lead: "A text with the link to apply, unless they were marked as not having come.",
    host: "",
    residents: "",
  },
];

// What the system does not do, by who does it.
const left: { who: string; items: string[] }[] = [
  {
    who: "The showing agent",
    items: [
      "Keep your calendar accurate. Anything busy on it blocks bookings, and a gap in it can be booked.",
      "Host the showing. The event on your calendar has who is coming and how to reach them.",
    ],
  },
  {
    who: "The team",
    items: [
      "Approve replies while approval is on, and answer what the agent hands over.",
      "Follow up when alerted: a message failed, a mobile number could not be texted, or a unit’s residents could not be looked up.",
      "Book someone who phones, on the Booking settings tab. The same people are told.",
      "After a showing, mark who came. Someone marked as a no-show is not sent the link to apply.",
    ],
  },
];

/** The diagram and the two sections below it. */
export function LeasingOverview() {
  return (
    <>
      <LeasingArchitecture />

      <section aria-labelledby="told-title" className="mt-9">
        <h2 id="told-title" className="font-semibold">Who hears what, and when</h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
          Every text, invite and calendar event below is sent by the system. Texts go only to a lead who agreed to them, and to residents only in the daytime.
        </p>
        <div className="mt-4 overflow-x-auto rounded-xl border">
          <table className="w-full min-w-[44rem] border-collapse text-left text-sm">
            <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th scope="col" className="w-[19%] px-4 py-3 font-semibold">When</th>
                <th scope="col" className="w-[33%] px-4 py-3 font-semibold">The lead</th>
                <th scope="col" className="w-[24%] px-4 py-3 font-semibold">The showing agent</th>
                <th scope="col" className="w-[24%] px-4 py-3 font-semibold">Current residents</th>
              </tr>
            </thead>
            <tbody className="align-top leading-6">
              {told.map((row) => (
                <tr key={row.when} className="border-t">
                  <th scope="row" className="px-4 py-3 font-medium">{row.when}</th>
                  {[row.lead, row.host, row.residents].map((cell, index) => (
                    <td key={index} className="px-4 py-3 text-muted-foreground">{cell || <span aria-label="Nothing">–</span>}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="left-title" className="mt-9">
        <h2 id="left-title" className="font-semibold">What is left to people</h2>
        <div className="mt-3 grid gap-6 sm:grid-cols-2">
          {left.map((group) => (
            <div key={group.who}>
              <h3 className="text-sm font-medium">{group.who}</h3>
              <ul className="mt-2 list-disc space-y-2 pl-5 text-sm leading-6 text-muted-foreground">
                {group.items.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
