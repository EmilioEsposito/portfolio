/**
 * Content for the evals map: the stages of evaluating an AI system, the
 * datasets they use, and the names the field gives each.
 *
 * Counts are real. Every example is made up: real cases hold applicant emails.
 */

export type Status = "done" | "now" | "next";
export type PhaseId = "cold-start" | "offline" | "online";
export type SplitId = "train" | "dev" | "test";

export interface ExampleBlock {
  label?: string;
  text: string;
}

interface Entry {
  id: string;
  /** The most common name for this thing. */
  term: string;
  /** Other names in use for the same thing. */
  aka: string[];
  oneLine: string;
  what: string[];
  /** What it is in this project, today. */
  here: string;
  example: { caption: string; blocks: ExampleBlock[] };
}

export interface Stage extends Entry {
  kind: "stage";
  phase: PhaseId;
  /** Part of the repeating loop inside its phase, or a one-time step. */
  loop: boolean;
  status: Status;
  /** Splits this stage reads from or writes to. */
  touches: SplitId[];
}

export interface Split extends Entry {
  kind: "split";
  id: SplitId;
  threads: number;
  cases: number;
  /** Test cases that have been run and annotated. */
  inReview: number;
}

export interface Phase {
  id: PhaseId;
  term: string;
  aka: string[];
  question: string;
  /** Shown beside the phase's loop, when it has one. */
  repeat?: string;
}

export type MapItem = Stage | Split;

export const phases: Phase[] = [
  {
    id: "cold-start",
    term: "Cold start",
    aka: ["bootstrapping", "before there is anything to measure"],
    question: "There are no outputs and no labels yet. What do we evaluate on?",
  },
  {
    id: "offline",
    term: "Offline evaluation",
    aka: ["pre-deployment evaluation", "development evals"],
    question: "Is this version good enough to ship?",
    repeat: "Repeat until new annotations reveal no new failure modes",
  },
  {
    id: "online",
    term: "Online evaluation",
    aka: ["post-deployment evaluation", "production monitoring"],
    question: "Is it still good on real traffic, and what is it getting wrong now?",
    repeat: "Back to offline evaluation, with new test cases",
  },
];

export const splits: Split[] = [
  {
    kind: "split",
    id: "train",
    term: "Train split",
    aka: ["few-shot examples", "training set"],
    threads: 21,
    cases: 43,
    inReview: 0,
    oneLine: "Worked examples that may be pasted into a prompt.",
    what: [
      "The train split is the pool of worked examples you are allowed to paste into a prompt. The name comes from machine learning; in a prompt-based system nothing is trained.",
      "Each example is an input paired with the output you want. For the agent's own prompt: a conversation and the reply a person would send. For an LLM judge's prompt: a reply with its pass or fail verdict and the reason.",
      "An example that is inside a prompt cannot also test that prompt, so these come from their own threads.",
    ],
    here: "Not used yet. Corrected replies written during annotation become candidates when their thread is in this split.",
    example: {
      caption: "A few-shot example for the agent's prompt",
      blocks: [
        {
          label: "Input",
          text: "Lead: Does 12 Example St #A have a dishwasher? I could tour Tuesday.",
        },
        {
          label: "Reference output",
          text: "Hello,\n\nYes, the apartment has a dishwasher. Tuesday at 10:00 AM is open. Does that work for you?\n\nSernia Capital Team",
        },
      ],
    },
  },
  {
    kind: "split",
    id: "dev",
    term: "Dev split",
    aka: ["validation set", "development set"],
    threads: 65,
    cases: 191,
    inReview: 30,
    oneLine: "The cases all iteration happens on.",
    what: [
      "All iteration happens on these cases: running experiments, reading outputs, annotating, and checking each change. They can be read as often as needed.",
      "Its score is optimistic, because every change made after reading dev results fits the system a little more closely to these particular cases.",
    ],
    here: "191 test cases. 30 of them have been run and annotated.",
    example: {
      caption: "What the dev split is for",
      blocks: [
        {
          text: "Experiment 2 on 30 dev cases\n→ annotate: 9 fails share one cause\n→ change the prompt\n→ experiment 3 on the same 30:\n   did those 9 pass, and did anything else break?",
        },
      ],
    },
  },
  {
    kind: "split",
    id: "test",
    term: "Test split",
    aka: ["held-out set", "holdout"],
    threads: 57,
    cases: 119,
    inReview: 0,
    oneLine: "Sealed until a version is believed finished.",
    what: [
      "Nobody runs or reads these until the iterating is done. Then a single run gives a pass rate that no change was fitted to.",
      "A test score far below the dev score means the changes fitted the dev cases without improving the system in general.",
      "A test split can be used this way only once. After one of its results informs a change, those cases count as dev, and new production cases refill the unseen pool.",
    ],
    here: "119 test cases. Thirty were run once, on the final policy before it shipped: no errors, no failed code checks, and the judge passed 25 of 30, against 48 of 60 on dev. Those 30 now count as dev and the other 89 are still sealed. They are not entirely unseen: before the split existed, every thread was read in aggregate to count what leads ask about and to copy unit facts out of our own replies.",
    example: {
      caption: "Reading the gap (an illustration, not a result)",
      blocks: [
        {
          text: "Dev pass rate    90%\nTest pass rate   72%\n\n→ fitted to dev. Back to error analysis, with more cases.",
        },
      ],
    },
  },
];

export const stages: Stage[] = [
  {
    kind: "stage",
    id: "inputs",
    phase: "cold-start",
    loop: false,
    status: "done",
    touches: [],
    term: "Test inputs",
    aka: ["seed data", "synthetic data, when there is no history"],
    oneLine: "Real past inputs, or generated ones when none exist.",
    what: [
      "An evaluation needs inputs before it needs anything else. If the product has history, use it: real inputs carry the real mix of easy and odd cases.",
      "With no history, generate synthetic inputs. Name the dimensions that vary (who is asking, about what, in which situation), write a few combinations by hand, then have a model turn each combination into a realistic message.",
      "Synthetic data can find failures, but it cannot say how common they are in real traffic.",
    ],
    here: "There was history: 143 real Zillow threads from April to June 2026. A small synthetic set is still planned, because real threads hold applicant details and cannot run in a public CI job.",
    example: {
      caption: "A synthetic input, built from dimensions",
      blocks: [
        {
          label: "Dimensions",
          text: "Who: student arriving from abroad\nAsks about: a video tour\nSituation: the unit is in-person only",
        },
        {
          label: "Generated message",
          text: "Hi! I start at CMU in August and I'm still overseas. Could you do a video tour of 12 Example St #A?",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "baseline",
    phase: "cold-start",
    loop: false,
    status: "done",
    touches: [],
    term: "Baseline prompt",
    aka: ["v0 prompt", "first draft"],
    oneLine: "A first version, written to have outputs to read.",
    what: [
      "Nothing can be evaluated until something produces outputs. Write the first prompt from what the business already knows, and expect it to be wrong in ways you cannot predict.",
      "This version only needs to produce outputs that can be read and annotated.",
    ],
    here: "The first policy was rewritten from the instructions the general-purpose agent had been following: when to reply, who qualifies, how to offer tour times, how to write.",
    example: {
      caption: "From the baseline policy",
      blocks: [
        {
          text: "Reply when a new lead has had no response from us yet.\n\nStay quiet when the next move is the lead's and they have not made it.\n\nEscalate when you cannot write a correct reply from the case. Do not guess.",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "dataset",
    phase: "cold-start",
    loop: false,
    status: "done",
    touches: [],
    term: "Dataset",
    aka: ["eval set", "golden dataset, once it has reference outputs"],
    oneLine: "353 test cases, cut from 143 threads.",
    what: [
      "A dataset is a list of test cases: each one input the system will be run on, plus whatever is needed to judge the result. A test case is also called a task or an example; this codebase calls it a scenario.",
      "One conversation can hold several test cases. Here a thread is cut at every point where the lead spoke last and a response was owed. Each cut holds only what was knowable at that moment.",
    ],
    here: "353 test cases from 143 threads. What was actually sent next is stored beside each one for the reviewer to see. It is not treated as the right answer.",
    example: {
      caption: "One thread, two test cases",
      blocks: [
        {
          label: "Thread",
          text: "1  Lead: Can I see it this week?\n2  Us:   Yes, Tuesday at 10 or 5:30.\n3  Lead: Tuesday at 10 works.\n4  Lead: Is there parking?",
        },
        {
          label: "Test cases",
          text: "A  message 1 only\nB  messages 1 to 4",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "split",
    phase: "cold-start",
    loop: false,
    status: "done",
    touches: ["train", "dev", "test"],
    term: "Split",
    aka: ["data split", "partition"],
    oneLine: "Assign every thread to train, dev or test, one time.",
    what: [
      "Before reading any results, divide the dataset into groups with different jobs, and never move anything between them.",
      "Split by the unit that shares information. Here that is the thread: a later test case contains the earlier ones, so splitting test cases one by one would leak a thread into two groups.",
    ],
    here: "21 threads to train, 65 to dev, 57 to test. The 30 test cases that had already been run were forced into dev.",
    example: {
      caption: "Why by thread",
      blocks: [
        {
          text: "Thread 17 → dev\n  cases 17-0, 17-3 and 17-5 all go to dev\n\nThread 18 → test\n  cases 18-0 and 18-2 both go to test",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "experiment",
    phase: "offline",
    loop: true,
    status: "done",
    touches: ["dev"],
    term: "Experiment",
    aka: ["eval run", "offline run"],
    oneLine: "Run one version over a sample and store every output.",
    what: [
      "An experiment runs one fixed version of the system over a set of test cases and stores each output with enough to reproduce it.",
      "A version is everything that can change the output: the prompt, the model, and any data the prompt is built from.",
    ],
    here: "Seventeen so far: ten on one sample of 30 dev cases, and seven on a second sample once the first stopped producing fails. Each records the policy's commit, the model and a hash of the listing data. The first showed that half the escalations were asking for facts the case did not contain; adding a fact sheet per unit cut escalations from 12 to 5.",
    example: {
      caption: "One stored output",
      blocks: [
        {
          text: "action         reply\nreason         New lead asking for a tour and one\n               question the fact sheet answers.\nreply_body     Hello, yes, the apartment has a…\noffered_slots  Tue 10:00, Tue 17:30\npolicy_commit  99b4199",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "annotation",
    phase: "offline",
    loop: true,
    status: "done",
    touches: ["dev"],
    term: "Human annotation",
    aka: ["labeling", "human review"],
    oneLine: "A person marks each output pass or fail, and says why.",
    what: [
      "One person who knows the domain reads each output and records a verdict and a short written reason. The verdict is binary, because the difference between a 3 and a 4 on a five-point scale is subjective and inconsistent.",
      "The next stage works from the written reasons, so they matter more than the verdicts.",
      "No rubric and no reference outputs are needed first. The criteria come from reading real outputs.",
    ],
    here: "Done for the first sample: 26 of 30 decisions annotated, 23 pass and 3 fail, in a small review page that shows what the lead wrote, what the model decided, and what was actually sent at the time. The four left out were the hardest to judge, so the pass rate is likely a little high.",
    example: {
      caption: "One annotation",
      blocks: [
        { label: "Verdict", text: "Fail" },
        {
          label: "Why",
          text: "Answers the dishwasher question but buries it under a long list of tour times. Lead with the answer and offer three or four times.",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "error-analysis",
    phase: "offline",
    loop: true,
    status: "done",
    touches: ["dev"],
    term: "Error analysis",
    aka: ["failure analysis", "open and axial coding"],
    oneLine: "Group the fail notes into named failure modes and count them.",
    what: [
      "Read the notes and give each failure a short name (open coding). Merge the names into a small set of categories (axial coding). The result is a failure taxonomy: the specific ways this system fails, with a count for each.",
      "The most frequent failure mode is the one to work on first. Without counts, effort tends to go to whichever failure was seen most recently.",
      "Stop adding cases when new annotations stop producing new failure modes. Thirty is a start; about a hundred is typical.",
    ],
    here: "Done for the first sample. The three fails gave two failure modes. Three is too few to rank with confidence, so the next sample is new cases from the dev split.",
    example: {
      caption: "The first failure taxonomy, from 26 annotated decisions",
      blocks: [
        {
          text: "Replied when our own earlier message\nconflicts with the case; should escalate   2\n\nOffered to move a tour that was\nalready confirmed                           1",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "improve",
    phase: "offline",
    loop: true,
    status: "now",
    touches: ["train", "dev"],
    term: "Improve",
    aka: ["iterate", "prompt engineering"],
    oneLine: "Fix the most common failure mode, in the layer that owns it.",
    what: [
      "Take the largest failure mode and change one thing. Run the experiment again on dev, and check that its count fell and no other rose.",
      "The thing changed is most often the prompt: the system prompt or skill, and any few-shot examples in it. But anything that shapes the output can be the fix: the context the prompt is built from, tool definitions and their descriptions, the output schema, the model and its settings, or the code around the call.",
      "Where the fix goes depends on the failure. A missing fact is a data problem. A rule with an exact answer belongs in code. Only judgement belongs in the prompt, sometimes as a few-shot example from the train split.",
    ],
    here: "The first fix from error analysis made things worse: a rule to escalate when our own earlier message conflicts with the case fixed two fails and broke six decisions that had passed. The labels on passing decisions caught it, and two narrower versions followed.",
    example: {
      caption: "Which layer owns the failure",
      blocks: [
        {
          text: "“Didn't know the unit has parking”\n  → data\n“Offered a time inside the notice period”\n  → code\n“Too stiff”, “should have waited”\n  → prompt\n“Asked for a discount”\n  → none. Escalating is correct.",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "evaluators",
    phase: "offline",
    loop: false,
    status: "next",
    touches: [],
    term: "Evaluators",
    aka: ["graders", "scorers", "metrics"],
    oneLine: "Automate each check: code where exact, an LLM judge where not.",
    what: [
      "Annotating by hand does not scale, so each stable failure mode gets an automatic check.",
      "A code evaluator (also: assertion, code-based grader) covers rules with exact answers. An LLM judge (also: LLM-as-judge, model-based grader) covers judgement: one judge per failure mode, each answering one yes-or-no question.",
      "Generic scores such as “helpfulness” or text similarity rarely correspond to the failure modes that error analysis found.",
    ],
    here: "Partly built. Code evaluators run on every reply: offered times are on the allowed list, the signature and a greeting are present, no staff names, no em-dash, links come from the case, the site address is written as a link, and a clarifying question comes right after the greeting. One LLM judge is built. It grades a decision against the whole policy on a stronger model than the one that writes replies, quotes the rule that was broken, and raises a question for the owner when the policy does not cover the case. It is not yet split into one judge per failure mode.",
    example: {
      caption: "The two kinds",
      blocks: [
        { label: "Code evaluator", text: "every offered time ∈ allowed times" },
        {
          label: "LLM judge, one question",
          text: "Does the reply answer the lead's question before it lists tour times? Answer pass or fail.",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "alignment",
    phase: "offline",
    loop: false,
    status: "next",
    touches: ["dev"],
    term: "Judge alignment",
    aka: ["calibration", "validating the judge"],
    oneLine: "Show the judge agrees with the person before trusting it.",
    what: [
      "An LLM judge is itself a prompt, and can be wrong. Before its verdicts count, compare them with human annotations.",
      "This needs a second split, of annotations this time. A few annotated outputs go into the judge's prompt as examples (its train). Most are used to refine the judge's wording (its dev). The rest are held back and scored once (its test).",
      "Report two numbers: how many good outputs it passes (true positive rate) and how many real fails it catches (true negative rate). A single agreement figure can look high for a judge that passes everything, when most outputs are good.",
    ],
    here: "Started. On 70 annotated outputs the judge failed all 10 that the reviewer had failed. It also failed 37 of the 60 he had passed. Of those, 33 break a rule added to the policy after he graded them, so the older passes cannot measure it. Four are real disagreements: two turn on a question the policy does not settle, and in the other two the judge looks too strict. The annotated outputs are split in two by thread, so the judge's prompt can be tuned on one half and scored on the other.",
    example: {
      caption: "A judge's report card (an illustration)",
      blocks: [
        {
          text: "On 40 held-back annotations:\n\nCatches real fails    18 of 20   TPR 90%\nPasses good replies   19 of 20   TNR 95%",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "held-out-test",
    phase: "offline",
    loop: false,
    status: "next",
    touches: ["test"],
    term: "Held-out test",
    aka: ["final evaluation", "test-set run"],
    oneLine: "One run on the test split, before shipping.",
    what: [
      "When dev results stop improving, freeze the version and run it once on the test split. Its pass rate is the one to report, because no change was fitted to these cases.",
      "It runs once. If its result is used to tune the system, the test split has become part of dev.",
    ],
    here: "Not started.",
    example: {
      caption: "The gate",
      blocks: [
        {
          text: "Version frozen at one commit\nRun once on 119 test cases\nPass rate within a few points of dev\n→ ship, with a person approving every send",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "release",
    phase: "online",
    loop: false,
    status: "next",
    touches: [],
    term: "Human-in-the-loop release",
    aka: ["HITL", "approval gate", "staged rollout"],
    oneLine: "Ship with a person approving every send.",
    what: [
      "In the first production version, every reply waits for a person to approve, edit or reject it.",
      "Approval can be relaxed as evidence accumulates, starting with the kinds of reply that are nearly always approved unchanged. Related tactics are shadow mode (generate, never send) and a canary (a small share of traffic).",
    ],
    here: "Planned. The approval gate already exists for the general-purpose agent, and this task will send through it.",
    example: {
      caption: "What the approver sees",
      blocks: [
        {
          text: "Draft reply to the lead at 12 Example St #A\n\n[ Approve ]  [ Edit ]  [ Reject ]\n\nCode sends it, to the thread's reply address.\nThe model never picks the recipient.",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "tracing",
    phase: "online",
    loop: true,
    status: "next",
    touches: [],
    term: "Tracing",
    aka: ["trace logging", "observability"],
    oneLine: "Record every production run in full.",
    what: [
      "A trace is the complete record of one run: the input, every model call and the output. Without traces there is nothing to evaluate after deployment.",
      "Offline, the dataset supplies the inputs and may hold a reference output. Online, real people supply the inputs and there is no reference output.",
    ],
    here: "Already in place for the application as a whole: every model call is traced, and experiments appear in the same tool as production runs. This task is not in production yet.",
    example: {
      caption: "One trace",
      blocks: [
        {
          text: "zillow_lead_decider run\n  input    the case, as text\n  model    one call, 4.1 s\n  output   action: reply, …\n  tags     policy commit, model",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "feedback",
    phase: "online",
    loop: true,
    status: "next",
    touches: [],
    term: "Feedback signals",
    aka: ["user feedback", "explicit and implicit feedback"],
    oneLine: "Capture what people did with each output.",
    what: [
      "Production generates labels at no extra cost when the application records them. Explicit feedback is a person's verdict; implicit feedback is what they did next.",
      "Feedback is biased toward what people notice. It misses the failures nobody reports, so it adds to trace review and does not replace it.",
    ],
    here: "Planned. Every approval is an annotation: sent unchanged is a pass, edited is a fail with the corrected reply attached, rejected is a fail.",
    example: {
      caption: "An edit is a label and a reference output",
      blocks: [
        {
          text: "Draft offers 38 tour times\nApprover cuts it to 4 and sends\n\n→ fail: “too many tour times”\n→ the sent reply becomes a reference output",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "online-evaluators",
    phase: "online",
    loop: true,
    status: "next",
    touches: [],
    term: "Online evaluators",
    aka: ["production monitoring", "guardrails, when they block"],
    oneLine: "Run the same automatic checks on live traces.",
    what: [
      "The evaluators built offline run on production traces: cheap code checks on every one, an LLM judge on a sample.",
      "An evaluator scores after the fact. A guardrail sits in the request path and blocks. “Offered a time that is not allowed” should be a guardrail here: that reply must not go out.",
    ],
    here: "Planned. The four code evaluators are cheap enough to run on every reply before approval.",
    example: {
      caption: "Evaluator or guardrail",
      blocks: [
        {
          text: "Offered time not on the allowed list\n  → guardrail: block the send\n\nAnswer buried under the tour times\n  → evaluator: score it, review the trend",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "trace-review",
    phase: "online",
    loop: true,
    status: "next",
    touches: [],
    term: "Trace review",
    aka: ["production error analysis", "annotation queue"],
    oneLine: "Sample live traces and read them for new failure modes.",
    what: [
      "This is error analysis applied to production traces. Always include some traces at random, and add the ones evaluators flagged or people edited.",
      "New failure modes appear here first because real inputs change over time, for example when new units are listed or a new rental season starts.",
    ],
    here: "Planned. A common rhythm is ten to twenty traces a week between larger passes.",
    example: {
      caption: "A week's sample",
      blocks: [
        {
          text: "10 at random\n 6 the approver edited\n 4 an evaluator flagged\n\n→ one new failure mode:\n  “asked about a unit listed since last week”",
        },
      ],
    },
  },
  {
    kind: "stage",
    id: "dataset-growth",
    phase: "online",
    loop: true,
    status: "next",
    touches: ["train", "dev", "test"],
    term: "Dataset growth",
    aka: ["data flywheel", "regression suite"],
    oneLine: "Turn production failures into test cases that stay.",
    what: [
      "Each production failure becomes a new test case. Once fixed, it stays in a regression suite that runs on every change to the prompt, the code or the model (continuous evaluation, or CI evals).",
      "New cases nobody has tuned against refill the test split.",
    ],
    here: "Planned. Real cases never run in a public CI job, so the suite in CI will be synthetic.",
    example: {
      caption: "From an edit to a regression test",
      blocks: [
        {
          text: "Edited reply in production\n→ new test case, with its corrected reply\n→ assigned to a split by its thread\n→ fails today, passes after the fix\n→ stays in the suite",
        },
      ],
    },
  },
];

export const items: MapItem[] = [...stages, ...splits];

export function findItem(id: string): MapItem | undefined {
  return items.find((item) => item.id === id);
}

/** The stage being worked on now. The map opens on it. */
export const currentStage = stages.find((stage) => stage.status === "now") ?? stages[0];

/** Splits to highlight while an item is active. */
export function litSplits(item: MapItem): SplitId[] {
  return item.kind === "split" ? [item.id] : item.touches;
}

/** Whether a stage should be highlighted while an item is active. */
export function stageIsRelated(stage: Stage, active: MapItem): boolean {
  return active.kind === "split" && stage.touches.includes(active.id);
}

export const totals = {
  threads: splits.reduce((sum, split) => sum + split.threads, 0),
  cases: splits.reduce((sum, split) => sum + split.cases, 0),
};

/** The whole process, short enough to redraw from memory. */
export const whiteboard: { phase: string; lines: string[] }[] = [
  {
    phase: "Cold start",
    lines: ["inputs → baseline prompt → dataset → split into train | dev | test"],
  },
  {
    phase: "Offline",
    lines: [
      "on dev:  experiment → annotate → error analysis → improve ↺",
      "then:    evaluators → judge alignment → one run on test",
    ],
  },
  {
    phase: "Online",
    lines: [
      "release behind approval",
      "tracing → feedback + online evaluators → trace review → dataset growth ↺",
      "new test cases flow back to offline",
    ],
  },
];

export const experimentLog = [
  {
    name: "Experiment 1",
    change: "Baseline prompt. Listings had rent and lease dates only.",
    reply: 12,
    quiet: 6,
    escalate: 12,
    annotated: "No",
  },
  {
    name: "Experiment 2",
    change: "Added a fact sheet per unit and standard answers.",
    reply: 20,
    quiet: 5,
    escalate: 5,
    annotated: "No",
  },
  {
    name: "Experiment 3",
    change:
      "Policy checked against the instructions it replaced: decline a low self-reported credit score, restored tour rules.",
    reply: 19,
    quiet: 5,
    escalate: 6,
    annotated: "No",
  },
  {
    name: "Experiment 4",
    change:
      "Tours offered through a booking link by default; times only for an already-booked tour or a time the lead named.",
    reply: 20,
    quiet: 4,
    escalate: 6,
    annotated: "No",
  },
  {
    name: "Experiment 5",
    change: "Answers that are the same for every lead moved from the case into the prompt.",
    reply: 19,
    quiet: 4,
    escalate: 7,
    annotated: "26 of 30: 23 pass",
  },
  {
    name: "Experiment 6",
    change:
      "First fixes from error analysis. The new escalation rule fired on almost any earlier message.",
    reply: 11,
    quiet: 2,
    escalate: 17,
    annotated: "Scored against labels: 19 of 26",
  },
  {
    name: "Experiment 8",
    change: "Escalation and confirmed-tour rules spelled out case by case.",
    reply: 17,
    quiet: 4,
    escalate: 9,
    annotated: "24 of 26",
  },
  {
    name: "Experiment 9",
    change: "Same prompt on a newer, cheaper model.",
    reply: 15,
    quiet: 4,
    escalate: 11,
    annotated: "23 of 26",
  },
  {
    name: "Experiment 10",
    change: "Booking line reworded to say \"in-person showing\".",
    reply: 18,
    quiet: 3,
    escalate: 9,
    annotated: "24 of 26",
  },
  {
    name: "Experiment 11",
    change: "No change. First run on a second sample of 30 dev cases.",
    reply: 13,
    quiet: 5,
    escalate: 12,
    annotated: "No",
  },
  {
    name: "Experiment 12",
    change:
      "No policy change. Tour state set by hand in 6 cases of the second sample, and only those run again.",
    reply: 17,
    quiet: 6,
    escalate: 7,
    annotated: "30 of 30: 26 pass",
  },
  {
    name: "Experiment 13",
    change:
      "Fixes from the second error analysis: a clarifying question leads the reply, a turned-down time is not offered again, an accepted time gets one confirming reply.",
    reply: 20,
    quiet: 3,
    escalate: 7,
    annotated: "No",
  },
  {
    name: "Experiment 14",
    change:
      "A lead who names a time we do not have gets the booking link, not a list of the closest times.",
    reply: 20,
    quiet: 3,
    escalate: 7,
    annotated: "No",
  },
  {
    name: "Experiment 15",
    change:
      "A time less than 24 hours away is not available, even when we offered it. First run graded by the LLM judge.",
    reply: 21,
    quiet: 2,
    escalate: 7,
    annotated: "By the judge: 20 of 30 pass",
  },
  {
    name: "Experiment 16",
    change:
      "Five policy questions answered by the owner directly, with a default offered for each, and three exact rules moved into code checks.",
    reply: 21,
    quiet: 3,
    escalate: 6,
    annotated: "By the judge: 25 of 30 pass",
  },
  {
    name: "Experiment 17",
    change:
      "Confirming run before merge. A detail the fact sheet marks as not on file is escalated, not answered with a no.",
    reply: 21,
    quiet: 3,
    escalate: 6,
    annotated: "By the judge: 28 of 30 pass",
  },
];

export const sources = [
  {
    author: "Hamel Husain and Shreya Shankar",
    title: "AI Evals: Everything You Need to Know",
    href: "https://hamel.dev/blog/posts/evals-faq/",
    used: "Error analysis, binary annotations, synthetic data, train / dev / test for a judge, offline and online evals, guardrails.",
  },
  {
    author: "Hamel Husain",
    title: "Using LLM-as-a-Judge",
    href: "https://hamel.dev/blog/posts/llm-judge/",
    used: "One domain expert, a written critique with every verdict, true positive and true negative rates.",
  },
  {
    author: "Eugene Yan",
    title: "Product Evals in Three Simple Steps",
    href: "https://eugeneyan.com/writing/product-evals/",
    used: "Label, align the evaluator on dev, check it on test; one evaluator per dimension.",
  },
  {
    author: "Anthropic",
    title: "Demystifying evals for AI agents",
    href: "https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents",
    used: "Tasks, trials, graders and transcripts; capability evals that graduate into a regression suite.",
  },
  {
    author: "LangSmith",
    title: "Evaluation concepts",
    href: "https://docs.langchain.com/langsmith/evaluation-concepts",
    used: "Dataset, example, experiment, evaluator, reference output; how offline and online evaluation feed each other.",
  },
  {
    author: "OpenAI",
    title: "Evaluation best practices",
    href: "https://developers.openai.com/api/docs/guides/evaluation-best-practices",
    used: "Where eval data comes from, calibrating automatic scoring against people, evaluating on every change.",
  },
];
