import OperationalCaseStudy, { operationsStudy, caseStudyMeta, caseStudyLinks } from "~/components/operational-case-study";

export const meta = () => caseStudyMeta(operationsStudy);
export const links = () => caseStudyLinks(operationsStudy);
export default function CaseStudyPage() { return <OperationalCaseStudy study={operationsStudy} />; }
