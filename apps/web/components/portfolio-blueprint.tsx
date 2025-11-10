"use client";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type {
  ExecutionWindowResult,
  OpportunityBlueprintResult,
} from "@/types/pydantic-ai";

function BulletList({
  title,
  items,
}: {
  title: string;
  items: string[];
}) {
  if (!items.length) return null;

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold text-muted-foreground">{title}</h4>
      <ul className="space-y-1 text-sm list-disc list-inside">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

export function PortfolioBlueprintCard({
  blueprint,
}: {
  blueprint: OpportunityBlueprintResult;
}) {
  return (
    <Card className="border-primary/20">
      <CardHeader>
        <CardTitle>{blueprint.working_title}</CardTitle>
        <CardDescription>{blueprint.north_star}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm leading-relaxed">{blueprint.elevator_pitch}</p>

        <BulletList title="Audience" items={blueprint.audience} />
        <BulletList
          title="Signature experiences"
          items={blueprint.signature_experiences}
        />
        <BulletList
          title="Launch milestones"
          items={blueprint.launch_milestones}
        />
        <BulletList
          title="Success metrics"
          items={blueprint.success_metrics}
        />

        <ExecutionWindowCard execution={blueprint.execution_window} />
      </CardContent>
    </Card>
  );
}

export function ExecutionWindowCard({
  execution,
}: {
  execution: ExecutionWindowResult;
}) {
  const formatter = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });

  return (
    <Card className="border-dashed">
      <CardHeader className="pb-4">
        <CardTitle className="text-base">Execution window</CardTitle>
        <CardDescription>
          {execution.cadence} • {execution.sprint_weeks} weeks • {" "}
          {formatter.format(execution.estimated_cost)} estimated investment
        </CardDescription>
      </CardHeader>
      <CardContent className="pt-0 space-y-2 text-sm">
        <p className="text-muted-foreground">{execution.notes}</p>
        <p className="font-medium">
          Runway assumption: {execution.runway_months.toFixed(1)} months
        </p>
      </CardContent>
    </Card>
  );
}
