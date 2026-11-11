import { Link } from "react-router";
import { ExternalLink } from "lucide-react";
import EvalsMap from "~/components/evals-map";
import { experimentLog, sources, totals, whiteboard } from "~/lib/evals-map";
import { buildUrl, generateCanonicalLink, generatePageMeta } from "~/lib/seo";

const page = {
  path: "/systems/leasing-evals",
  title: "Evaluating an AI leasing agent",
  description:
    "The stages of evaluating an AI agent before and after it ships, the datasets each stage uses, and the names the field gives each, shown on a real project in progress.",
};

export const meta = () => generatePageMeta(page);
export const links = () => [generateCanonicalLink(buildUrl(page.path))];

export default function LeasingEvalsPage() {
  return (
    <main className="mx-auto w-full max-w-6xl px-5 py-10 sm:px-8 sm:py-14">
      <Link
        to="/systems/leasing-leads"
        className="text-sm text-muted-foreground underline underline-offset-4"
      >
        Back to the leasing agent
      </Link>
      <header className="mt-8 max-w-3xl">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-5xl">{page.title}</h1>
        <p className="mt-5 text-lg leading-8 text-muted-foreground">
          When a rental inquiry arrives, the agent decides to reply, stay quiet, or hand the thread
          to a person. This page shows how that decision is measured: each stage, the data it
          uses, and the name the stage usually goes by.
        </p>
        <p className="mt-4 text-sm leading-6 text-muted-foreground">
          The project is in progress. The counts are real ({totals.cases} test cases from{" "}
          {totals.threads} email threads), and every example is made up because the real ones hold
          applicant details.
        </p>
      </header>

      <section aria-labelledby="map-title" className="mt-10">
        <h2 id="map-title" className="text-xl font-semibold">
          The map
        </h2>
        <p className="mb-5 mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
          Hover or tap any stage or dataset. A stage lights up the data it uses; a dataset lights up
          the stages that use it.
        </p>
        <EvalsMap />
      </section>

      <section aria-labelledby="whiteboard-title" className="mt-14 max-w-3xl">
        <h2 id="whiteboard-title" className="text-xl font-semibold">
          The whiteboard version
        </h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          The same process, condensed enough to redraw from memory.
        </p>
        <dl className="mt-4 divide-y rounded-2xl border">
          {whiteboard.map((row) => (
            <div key={row.phase} className="grid gap-1 p-4 sm:grid-cols-[7rem_1fr] sm:gap-4">
              <dt className="text-sm font-semibold">{row.phase}</dt>
              <dd className="space-y-1 text-sm leading-6">
                {row.lines.map((line) => (
                  <p key={line} className="whitespace-pre-wrap">
                    {line}
                  </p>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <section aria-labelledby="log-title" className="mt-14 max-w-3xl">
        <h2 id="log-title" className="text-xl font-semibold">
          What the first two experiments showed
        </h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          Both experiments ran on the same 30 test cases, before and after one change. In the
          first, most escalations were the agent asking for facts it had not been given, so the fix
          was to the data. Neither experiment measures whether the replies are good; the
          annotations will.
        </p>
        <div className="mt-4 overflow-x-auto rounded-2xl border">
          <table className="w-full min-w-[34rem] text-left text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr className="border-b">
                <th scope="col" className="p-3 font-medium">
                  Experiment
                </th>
                <th scope="col" className="p-3 font-medium">
                  What changed
                </th>
                <th scope="col" className="p-3 text-right font-medium">
                  Reply
                </th>
                <th scope="col" className="p-3 text-right font-medium">
                  Stay quiet
                </th>
                <th scope="col" className="p-3 text-right font-medium">
                  Escalate
                </th>
                <th scope="col" className="p-3 font-medium">
                  Annotated
                </th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {experimentLog.map((row) => (
                <tr key={row.name}>
                  <th scope="row" className="whitespace-nowrap p-3 font-medium">
                    {row.name}
                  </th>
                  <td className="p-3 text-muted-foreground">{row.change}</td>
                  <td className="p-3 text-right tabular-nums">{row.reply}</td>
                  <td className="p-3 text-right tabular-nums">{row.quiet}</td>
                  <td className="p-3 text-right tabular-nums">{row.escalate}</td>
                  <td className="p-3 text-muted-foreground">{row.annotated}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="names-title" className="mt-14 max-w-3xl">
        <h2 id="names-title" className="text-xl font-semibold">
          About the names
        </h2>
        <div className="mt-2 space-y-3 text-sm leading-6 text-muted-foreground">
          <p>
            The field has not settled on one vocabulary. Each stage above carries the name used most
            often, with the others listed as aka. The same check is an evaluator, a grader or a
            scorer depending on the tool, and the same split is dev or validation depending on
            the author&apos;s background.
          </p>
          <p>
            Train, dev and test come from machine learning and are the usual names wherever splits
            are discussed. The published advice applies them to the annotated data used to build an
            LLM judge. Using the same three splits while tuning the agent&apos;s own prompt is this
            project&apos;s extension of that reasoning.
          </p>
          <p>
            Two different things therefore get split: test cases, to tune the agent, and later
            the annotations made on dev, to tune the judge. Both splits use the names train, dev
            and test.
          </p>
          <p>
            Cold start is the least standard name here. The sources describe the phase, usually as
            what to do before there is production traffic, without agreeing on what to call it.
          </p>
        </div>
      </section>

      <section aria-labelledby="sources-title" className="mt-14 max-w-3xl">
        <h2 id="sources-title" className="text-xl font-semibold">
          Sources
        </h2>
        <ul className="mt-4 space-y-4">
          {sources.map((source) => (
            <li key={source.href} className="text-sm leading-6">
              <a
                href={source.href}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium underline underline-offset-4"
              >
                {source.title}
              </a>
              <span className="text-muted-foreground">, {source.author}</span>
              <p className="text-muted-foreground">{source.used}</p>
            </li>
          ))}
        </ul>
      </section>

      <footer className="mt-12 flex flex-wrap items-center gap-6 border-t pt-7">
        <a
          href="https://github.com/EmilioEsposito/portfolio/tree/main/api/src/sernia_ai/zillow_leads"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 text-sm underline underline-offset-4"
        >
          Read the implementation <ExternalLink aria-hidden="true" className="size-4" />
        </a>
        <Link to="/systems/leasing-leads" className="text-sm underline underline-offset-4">
          How the leasing agent works
        </Link>
      </footer>
    </main>
  );
}
