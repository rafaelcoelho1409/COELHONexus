# COELHO Nexus — design contract

Operator-console, not brochure: graphite surfaces, one oxblood accent, hairline borders, monospace for
metadata. The interface shows the product working; it does not describe it with adjectives.
Everything below is enforced by a script or a test unless marked *(review)*.

## Colour

Generated, never hand-written: `npm run tokens` → `src/shared/styles/tokens.generated.css`.
Three inputs in `scripts/generate-tokens.mjs`: neutral base (hue 265), accent (oxblood, hue 22), contrast.
Output is Radix's 12-step roles in OKLCH for `neutral`, `accent`, `success`, `warning`, `danger`, `running`,
in four themes: dark, light, and a high-contrast variant of each (applied by `prefers-contrast: more`).

| Steps | Role |
| --- | --- |
| 1–2 | app surface, card surface |
| 3–5 | component surface: rest, hover, active |
| 6–8 | border: decorative, interactive, strong |
| 9–10 | solid fill: rest, hover (`--*-contrast` is the label colour on it) |
| 11–12 | text: secondary, primary |

Components use the semantic roles in `globals.css` (`bg-card`, `text-muted-foreground`, `text-primary-text`,
`border-border`), not raw steps, except for one-off hairlines (`border-neutral-8`).
`npm run tokens:check` fails if the file is stale **or** a contrast rule breaks in any theme:
primary text ≥ 10:1, secondary text ≥ 4.5:1 on both surfaces, form borders ≥ 3:1, every status colour's
text ≥ 4.5:1, every solid's label ≥ 4.5:1. Never use colour alone to carry meaning — pair it with text.

## Type

Archivo (UI) and JetBrains Mono (ids, units, metadata). **12px is the floor** — no `text-[10px]`, no
`text-[11px]`; `e2e/home.spec.ts` measures every rendered text node.

| Token | Size | Use |
| --- | --- | --- |
| `text-xs` | 12px | `.mono-label`, captions, table metadata |
| `text-sm` | 13px | dense UI, buttons, nav |
| `text-base` | 15px | body |
| `text-lg` / `text-xl` | 18 / 22px | lead copy, panel figures |
| `text-3xl`+ | 30px+ | section and hero headings (`text-balance`, tight leading) |

Numbers that change use `tabular` (tabular-nums). Uppercase is for `.mono-label` only.

## Shape, density, spacing

- Radius by role: controls 2px, surfaces 4px, overlays 8px. No pills, no `rounded-2xl` cards.
- Borders are 1px hairlines (`border-border`); depth comes from surface steps, not shadows. No glow, no
  gradient fills, no glass blur.
- Density: `--row-height` is 40px (comfortable) or 32px (`data-density="compact"` on `<html>`), for the
  data-heavy screens that follow. Marketing sections use `py-16 md:py-24`.
- Layout container `max-w-6xl`, gutters 16px (mobile) / 24px. Single-column grids use `grid-cols-1`
  (`minmax(0,1fr)`) so a long code line can never widen the page.

## Motion

Almost none. The only animation is the 3s status pulse; it is off under `prefers-reduced-motion`.
Interactions change colour (150ms). Nothing scrolls, parallaxes, or fades in on view.

## Content policy

The site describes what the products do and how the platform is operated. It never names the model-routing
layer, the model providers behind it, bandit/router internals, "free-tier", BYOK, or retired products
(Research Radar). `npm run copy:check` scans `src/app`, `src/features`, `src/shared/ui` and `public` and
fails the build on a hit. The OpenAPI exporter applies the same rule to the published API schema.
Figures on the page are read from the API (`PlatformPanel`); never type a number in by hand.

## Accessibility

WCAG 2.2 AA is the bar, checked with axe in dark and light at desktop and phone width
(`e2e/home.spec.ts`). Skip link first; one `h1`; sections are landmarks with `aria-labelledby`; visible 2px
focus ring in every theme; a horizontally scrolling region must be focusable; targets ≥ 36px.

## Anti-slop checklist *(review before merging UI)*

- [ ] No purple/indigo gradient, no gradient text, no glass cards, no glowing borders.
- [ ] No emoji or stock icon as decoration; icons are Lucide at 16px, only where they aid scanning.
- [ ] No "revolutionary / seamless / powerful / next-gen" copy; every claim is a fact the page can show.
- [ ] No row of three identical icon-title-blurb cards as the page's only structure.
- [ ] Real data over mock data; empty and error states designed, not blank.
- [ ] Hierarchy comes from size, weight and spacing — not from boxes around everything.
- [ ] Works at 375px, in light and dark, with the keyboard only.

## Budgets

First load of `/`: JS ≤ 170 KB gz, CSS ≤ 30 KB gz, HTML ≤ 40 KB gz (`npm run budgets`, excludes Next's
`noModule` polyfills). Zod stays out of the first-load path; forms and env validation are the places for it.
