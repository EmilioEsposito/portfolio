export interface ExecutionWindowResult {
  sprint_weeks: number;
  cadence: string;
  estimated_cost: number;
  runway_months: number;
  notes: string;
}

export interface OpportunityBlueprintResult {
  working_title: string;
  north_star: string;
  elevator_pitch: string;
  audience: string[];
  signature_experiences: string[];
  launch_milestones: string[];
  success_metrics: string[];
  execution_window: ExecutionWindowResult;
}
