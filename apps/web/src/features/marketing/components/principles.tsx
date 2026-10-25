import { PRINCIPLES } from "../content";

export function Principles() {
  return (
    <section id="principles" aria-labelledby="principles-title" className="border-b border-border">
      <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6 md:py-24">
        <p className="mono-label">Principles</p>
        <h2
          id="principles-title"
          className="mt-4 max-w-2xl text-balance text-3xl font-semibold tracking-tight"
        >
          How it is built.
        </h2>
        <ul className="mt-12 grid grid-cols-1 gap-10 md:grid-cols-3 md:gap-8">
          {PRINCIPLES.map((principle) => (
            <li key={principle.title} className="border-t border-border pt-6">
              <p className="mono-label">{principle.index}</p>
              <h3 className="mt-3 text-lg font-semibold tracking-tight">{principle.title}</h3>
              <p className="mt-3 text-pretty text-base text-muted-foreground">{principle.body}</p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
