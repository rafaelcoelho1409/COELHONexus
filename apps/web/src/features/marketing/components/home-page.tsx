import { Hero } from "./hero";
import { Principles } from "./principles";
import { Products } from "./products";
import { SiteFooter } from "./site-footer";
import { SiteHeader } from "./site-header";
import { SourceCta } from "./source-cta";
import { System } from "./system";

export function HomePage() {
  return (
    <>
      <SiteHeader />
      <main id="main">
        <Hero />
        <Products />
        <System />
        <Principles />
        <SourceCta />
      </main>
      <SiteFooter />
    </>
  );
}
