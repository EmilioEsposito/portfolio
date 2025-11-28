import { useState } from "react";
import type { Route } from "./+types/examples.multi-select";
import { MultiSelect } from "~/components/multi-select";
import { Cat, Dog, Fish, Rabbit, Turtle } from "lucide-react";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Multi-Select Example | Emilio Esposito" },
    {
      name: "description",
      content:
        "A reusable multi-select component built with Shadcn UI and Radix UI",
    },
  ];
}

const frameworksList = [
  { value: "react", label: "React", icon: Turtle },
  { value: "angular", label: "Angular", icon: Cat },
  { value: "vue", label: "Vue", icon: Dog },
  { value: "svelte", label: "Svelte", icon: Rabbit },
  { value: "ember", label: "Ember", icon: Fish },
];

export default function MultiSelectExample() {
  const [selectedFrameworks, setSelectedFrameworks] = useState<string[]>([
    "react",
    "angular",
  ]);

  return (
    <div className="p-4 max-w-xl">
      <h1 className="text-2xl font-bold mb-4">Multi-Select Component</h1>
      <MultiSelect
        options={frameworksList}
        onValueChange={setSelectedFrameworks}
        defaultValue={selectedFrameworks}
        placeholder="Select frameworks"
        variant="inverted"
        animation={2}
        maxCount={3}
      />
      <div className="mt-4">
        <h2 className="text-xl font-semibold">Selected Frameworks:</h2>
        <ul className="list-disc list-inside">
          {selectedFrameworks.map((framework) => (
            <li key={framework}>{framework}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
