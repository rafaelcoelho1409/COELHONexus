import { type AnsiSegment, parseAnsi } from "./ansi";

function classesOf(segment: AnsiSegment): string | undefined {
  const classes = [
    segment.tone ? `ansi-${segment.tone}` : null,
    segment.bold ? "ansi-bold" : null,
    segment.dim ? "ansi-dim" : null,
    segment.italic ? "ansi-italic" : null,
    segment.underline ? "ansi-underline" : null,
  ].filter((name): name is string => name !== null);
  return classes.length > 0 ? classes.join(" ") : undefined;
}

/** Terminal output (a ```ansi or ```terminal fence): the colour codes become coloured text, nothing else is run. */
export function TerminalBlock({ source }: { source: string }) {
  // Segments carry no id; number them once so React keys stay stable.
  const segments = parseAnsi(source).map((segment, id) => ({ id, segment }));
  return (
    <figure className="md-terminal" aria-label="Terminal output">
      {/* biome-ignore lint/a11y/noNoninteractiveTabindex: long lines scroll sideways, so keyboard users must be able to focus the block to scroll it (WCAG 2.1.1) */}
      <pre tabIndex={0}>
        <code>
          {segments.map(({ id, segment }) => (
            <span key={id} className={classesOf(segment)}>
              {segment.text}
            </span>
          ))}
        </code>
      </pre>
    </figure>
  );
}
