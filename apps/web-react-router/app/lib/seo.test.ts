import assert from "node:assert/strict";
import test from "node:test";
import { generateJsonLd } from "./seo";

test("JSON-LD remains an object after React Router serializes the meta descriptor", () => {
  const schema = {
    "@context": "https://schema.org",
    "@type": "ProfilePage",
    mainEntity: { "@type": "Person", name: "Emilio Esposito" },
  };
  // Meta serializes this field itself. Passing a JSON string used to double-encode it.
  const renderedContent = JSON.stringify(generateJsonLd(schema)["script:ld+json"]);
  const parsed = JSON.parse(renderedContent);
  assert.equal(parsed["@type"], "ProfilePage");
  assert.equal(parsed.mainEntity.name, "Emilio Esposito");
});
