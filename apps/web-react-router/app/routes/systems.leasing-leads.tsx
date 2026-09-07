import OperationalCaseStudy, { leasingStudy, caseStudyMeta, caseStudyLinks } from "~/components/operational-case-study";

export const meta = () => caseStudyMeta(leasingStudy);
export const links = () => caseStudyLinks(leasingStudy);
export default function CaseStudyPage() { return <OperationalCaseStudy study={leasingStudy} />; }
