import { H1, H2, H3, P } from "@/components/typography";

export default function Home() {
  return (
    <div className="p-4">
      <H1>Header 1</H1>
      <P>Emilio Esposito</P>
      <P>
        Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod
        tempor incididunt ut labore et dolore magna aliqua.
      </P>

      <H2>Header 2</H2>
      <P>
        Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod
        tempor incididunt ut labore et dolore magna aliqua.
      </P><br/>
      <H3>Header 3</H3>
      <P>
        Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod
        tempor incididunt ut labore et dolore magna aliqua.
      </P>
    </div>
  );
}
