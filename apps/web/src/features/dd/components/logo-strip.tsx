/** A framework's logo(s) as images. The catalog hands over hot-linked URLs; they are decorative (the name always
 * sits beside them), so they carry an empty alt, load only when scrolled to, and send no referrer to the
 * project's own site. A framework with no logo gets its first letter in a small frame instead. */

type LogoStripProps = {
  logos: readonly string[];
  /** Used for the letter fallback only. */
  name: string;
  /** Show at most this many logos (a multi-logo entry is a strip of marks). */
  max?: number;
  /** Pixel-based box height class; the image keeps its aspect ratio inside it. */
  size?: "sm" | "md";
};

const IMAGE_HEIGHT = { sm: "h-6 max-w-20", md: "h-8 max-w-28" } as const;
const LETTER_BOX = { sm: "size-6 text-xs", md: "size-8 text-sm" } as const;

export function LogoStrip({ logos, name, max = 3, size = "md" }: LogoStripProps) {
  const shown = logos.slice(0, max);
  if (shown.length === 0) {
    return (
      <span
        aria-hidden="true"
        className={`grid shrink-0 place-items-center rounded-sm border border-border font-mono text-muted-foreground ${LETTER_BOX[size]}`}
      >
        {name.slice(0, 1).toUpperCase()}
      </span>
    );
  }
  return (
    <>
      {shown.map((src) => (
        // biome-ignore lint/performance/noImgElement: remote logos of arbitrary size and host — next/image would need every host allow-listed
        <img // eslint-disable-line @next/next/no-img-element
          key={src}
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          className={`w-auto shrink-0 object-contain ${IMAGE_HEIGHT[size]}`}
        />
      ))}
    </>
  );
}
