import { REPO_URL } from "../params";
import { BrandMark } from "./brand-mark";

export function SiteFooter() {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <p className="flex items-center gap-2.5">
          <BrandMark className="size-4 text-primary-text" />
          <span>COELHO Nexus</span>
        </p>
        <a href={REPO_URL} className="transition-colors hover:text-foreground">
          github.com/rafaelcoelho1409/COELHONexus
        </a>
      </div>
    </footer>
  );
}
