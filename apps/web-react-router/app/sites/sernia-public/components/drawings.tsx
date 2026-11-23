/**
 * Sernia Capital's logo mark and the street on the home page, drawn in the
 * mark's line style. Both take their colors from `.sernia-public` in app.css,
 * so they follow the theme, and both are decoration: hidden from screen readers.
 */

/** The mark, without the name. `public/images/sernia/icon.svg` is the same drawing. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 743 743" aria-hidden="true" className={className}>
      <polygon
        className="fill-sernia-ink"
        points="338,58 11,393 136,393 136,643 281,643 237,689 269,720 366,620 269,522 237,553 281,598 182,598 182,348 119,348 369,90"
      />
      <polygon
        className="fill-sernia-sky"
        points="369,28 732,389 606,389 606,643 404,643 404,393 296,393 296,348 449,348 449,598 561,598 561,345 623,345 338,59"
      />
    </svg>
  );
}

// One length of the street, generated once from the approved mockup. It starts
// at x = -12 and repeats every STREET_LENGTH, with no visible join.
const STREET_LENGTH = 1392;
const HOUSES =
  "M-12 130h112v182h-112zM144 130h96v182h-96zM284 144h120v168h-120zM404 144h104v168h-104zM508 144h104v168h-104zM612 98h120v214h-120zM732 130h104v182h-104zM836 144h112v168h-112zM992 98h120v214h-120zM1112 116h112v196h-112zM1268 98h112v214h-112z";
const SKY_LINES =
  "M102 254a20 20 0 1 0 40 0a20 20 0 1 0 -40 0zM144 130h96v182h-96zM140 130L244 130M144 142L240 142M163 158h19v34h-19zM202 158h19v34h-19zM163 210h19v34h-19zM202 210h19v34h-19zM157 262h25v32h-25zM242 254a20 20 0 1 0 40 0a20 20 0 1 0 -40 0zM950 254a20 20 0 1 0 40 0a20 20 0 1 0 -40 0zM1226 254a20 20 0 1 0 40 0a20 20 0 1 0 -40 0zM1268 98h112v214h-112zM1264 98L1384 98M1268 110L1380 110M1290 126h22v34h-22zM1335 126h22v34h-22zM1290 178h22v34h-22zM1335 178h22v34h-22zM1333 258h27v54h-27zM1284 262h29v32h-29z";
const SKY_DOORS = "M200 258h23v54h-23zM1177 258h27v54h-27z";
const INK_LINES =
  "M-12 130h112v182h-112zM-16 130L104 130M-12 142L100 142M10 158h22v34h-22zM55 158h22v34h-22zM10 210h22v34h-22zM55 210h22v34h-22zM4 258h27v54h-27zM53 262h29v32h-29zM122 274V312M262 274V312M284 144h120v168h-120zM280 144L408 144M284 156L404 156M308 172h24v34h-24zM356 172h24v34h-24zM301 258h29v54h-29zM354 262h31v32h-31zM404 144h104v168h-104zM404 144L456 98L508 144M425 172h21v34h-21zM466 172h21v34h-21zM419 258h25v54h-25zM464 262h27v32h-27zM508 144h104v168h-104zM508 144L560 98L612 144M529 172h21v34h-21zM570 172h21v34h-21zM523 258h25v54h-25zM568 262h27v32h-27zM612 98h120v214h-120zM612 98L624 60L720 60L732 98M661 70h22v26h-22zM636 126h24v34h-24zM684 126h24v34h-24zM636 178h24v34h-24zM684 178h24v34h-24zM682 258h29v54h-29zM629 262h31v32h-31zM732 130h104v182h-104zM728 130L840 130M732 142L836 142M753 158h21v34h-21zM794 158h21v34h-21zM753 210h21v34h-21zM794 210h21v34h-21zM792 258h25v54h-25zM747 262h27v32h-27zM836 144h112v168h-112zM832 144L952 144M836 156L948 156M858 172h22v34h-22zM903 172h22v34h-22zM852 258h27v54h-27zM901 262h29v32h-29zM970 274V312M992 98h120v214h-120zM988 98L1116 98M992 110L1112 110M1016 126h24v34h-24zM1064 126h24v34h-24zM1016 178h24v34h-24zM1064 178h24v34h-24zM1009 258h29v54h-29zM1062 262h31v32h-31zM1112 116h112v196h-112zM1112 116L1124 78L1212 78L1224 116M1157 88h22v26h-22zM1134 144h22v34h-22zM1179 144h22v34h-22zM1134 196h22v34h-22zM1179 196h22v34h-22zM1128 262h29v32h-29zM1246 274V312M-12 312H1380";

/**
 * A row of houses across the whole width, whatever it is: the drawing keeps
 * its height, centered, and repeats to both sides. Its element clips the rest.
 */
export function Street({ className }: { className?: string }) {
  return (
    <div aria-hidden="true" className="flex justify-center overflow-hidden">
      <svg viewBox="0 22 1400 308" className={`block w-auto max-w-none shrink-0 overflow-visible ${className ?? ""}`}>
        <defs>
          <g id="sernia-street" strokeWidth={5} fill="none">
            <path d={HOUSES} className="fill-sernia-house" />
            <path d={SKY_LINES} className="stroke-sernia-sky" />
            <path d={SKY_DOORS} className="fill-sernia-sky stroke-sernia-sky" />
            <path d={INK_LINES} className="stroke-sernia-ink" />
          </g>
        </defs>
        {[-2, -1, 0, 1, 2].map((step) => (
          <use key={step} href="#sernia-street" x={step * STREET_LENGTH} />
        ))}
      </svg>
    </div>
  );
}
