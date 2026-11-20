import type { ReactNode } from "react";
import { readerFont } from "@/shared/markdown/reader-font";

export function ReaderPageFrame({
  ariaLabel,
  label,
  title,
  titleId,
  subtitle,
  toolbar,
  headerContent,
  children,
}: {
  ariaLabel: string;
  label: string;
  title: string;
  titleId?: string;
  subtitle: string;
  toolbar?: ReactNode;
  headerContent?: ReactNode;
  children: ReactNode;
}) {
  return (
    <article aria-label={ariaLabel} className="min-w-0 rounded-md border border-border bg-card p-4 sm:p-5">
      <header className="mb-5 border-b border-border pb-4">
        <div className="flex items-center justify-between gap-2">
          <p className="mono-label">{label}</p>
          {toolbar}
        </div>
        <h2
          id={titleId}
          className={`${readerFont.className} mt-2 break-words text-2xl font-semibold tracking-tight`}
        >
          {title}
        </h2>
        <p className={`${readerFont.className} mt-1 break-words text-base text-muted-foreground`}>
          {subtitle}
        </p>
        {headerContent}
      </header>
      {children}
    </article>
  );
}
