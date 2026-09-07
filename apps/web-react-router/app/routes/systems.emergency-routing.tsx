import OperationalCaseStudy, { emergencyStudy, caseStudyMeta, caseStudyLinks } from "~/components/operational-case-study";

export const meta = () => caseStudyMeta(emergencyStudy);
export const links = () => caseStudyLinks(emergencyStudy);
export default function CaseStudyPage() { return <OperationalCaseStudy study={emergencyStudy} />; }
