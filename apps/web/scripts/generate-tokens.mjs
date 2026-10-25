// Generates the colour tokens from a handful of inputs, the way Linear generates its themes (base, accent,
// contrast) on top of Radix's 12-step roles, in OKLCH so equal lightness reads as equal lightness.
//
//   node scripts/generate-tokens.mjs          write src/shared/styles/tokens.generated.css
//   node scripts/generate-tokens.mjs --check  fail if the file is stale or any contrast rule is broken
//
// Step roles (Radix): 1-2 app/subtle surface · 3-5 component surface (rest/hover/active) · 6-8 borders
// (decorative/interactive/strong) · 9-10 solid (rest/hover) · 11 low-contrast text · 12 high-contrast text.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { clampChroma, wcagContrast } from "culori";

const OUT = join(
  resolve(dirname(fileURLToPath(import.meta.url)), ".."),
  "src/shared/styles/tokens.generated.css",
);

// ---- the inputs -------------------------------------------------------------------------------------------
const INPUTS = {
  neutral: { hue: 265, chroma: 0.014 }, // cool graphite surfaces and borders
  textHue: 85, // warm ivory text on graphite — the one deliberate temperature contrast
  accent: { hue: 22, chroma: 0.19 }, // oxblood
  status: {
    success: { hue: 152, chroma: 0.15 },
    warning: { hue: 78, chroma: 0.14 },
    danger: { hue: 27, chroma: 0.2 },
    running: { hue: 232, chroma: 0.14 },
  },
};

// Lightness ramps per theme. Steps 11-12 (text) and 6-8 (borders) are interpolated between a soft and a high
// contrast ramp by `contrast` (0 soft … 1 high); `default` themes use 0.5, the high-contrast variant 1.
const RAMPS = {
  dark: {
    neutral: [0.15, 0.175, 0.205, 0.235, 0.265, 0.3, 0.35, 0.42, 0.55, 0.6],
    text: { soft: [0.7, 0.9], high: [0.8, 0.98] },
    chromatic: [0.17, 0.2, 0.25, 0.29, 0.33, 0.38, 0.44, 0.52],
    solid: 0.5,
    colorText: { soft: [0.7, 0.9], high: [0.78, 0.96] },
  },
  light: {
    neutral: [0.995, 0.98, 0.96, 0.94, 0.92, 0.89, 0.85, 0.79, 0.56, 0.51],
    text: { soft: [0.5, 0.3], high: [0.4, 0.15] },
    chromatic: [0.985, 0.965, 0.94, 0.915, 0.89, 0.86, 0.82, 0.76],
    solid: 0.5,
    colorText: { soft: [0.5, 0.3], high: [0.4, 0.2] },
  },
};
const CHROMA_PROFILE = [0.06, 0.12, 0.25, 0.35, 0.45, 0.55, 0.7, 0.85, 1.0, 0.95, 0.9, 0.35];
const BORDER_STEPS_BY_CONTRAST = { dark: [0.012, 0.02], light: [-0.012, -0.02] }; // shift of steps 7-8 at contrast 1

// The accent solid is the brand colour in every theme; other solids are tuned per hue to carry their label.
const SOLID_LIGHTNESS = { accent: 0.47, success: 0.52, warning: 0.78, danger: 0.55, running: 0.52 };

// ---- helpers ----------------------------------------------------------------------------------------------
const lerp = (a, b, t) => a + (b - a) * t;
const round = (n, digits) => Number(n.toFixed(digits));

function color(l, c, h) {
  const fitted = clampChroma({ mode: "oklch", l, c, h }, "oklch");
  return { mode: "oklch", l: fitted.l, c: fitted.c ?? 0, h: fitted.h ?? h };
}
const css = ({ l, c, h }) => `oklch(${round(l, 4)} ${round(c, 4)} ${round(h, 1)})`;

function neutralScale(theme, contrast) {
  const ramp = RAMPS[theme];
  const lightness = [...ramp.neutral];
  const [t11, t12] = [
    lerp(ramp.text.soft[0], ramp.text.high[0], contrast),
    lerp(ramp.text.soft[1], ramp.text.high[1], contrast),
  ];
  lightness[6] += BORDER_STEPS_BY_CONTRAST[theme][0] * contrast * 10;
  lightness[7] += BORDER_STEPS_BY_CONTRAST[theme][1] * contrast * 10;
  lightness.push(t11, t12);
  const { hue, chroma } = INPUTS.neutral;
  return lightness.map((l, i) =>
    // surfaces and borders carry the neutral hue; the two text steps carry the warm text hue
    i >= 10 ? color(l, chroma * 1.2, INPUTS.textHue) : color(l, chroma * (0.6 + CHROMA_PROFILE[i]), hue),
  );
}

function chromaticScale(theme, name, spec, contrast) {
  const ramp = RAMPS[theme];
  const solid = SOLID_LIGHTNESS[name];
  const text = [
    lerp(ramp.colorText.soft[0], ramp.colorText.high[0], contrast),
    lerp(ramp.colorText.soft[1], ramp.colorText.high[1], contrast),
  ];
  const lightness = [...ramp.chromatic, solid, Math.max(solid - 0.04, 0.2), ...text];
  return lightness.map((l, i) => color(l, spec.chroma * CHROMA_PROFILE[i], spec.hue));
}

const ink = (theme) => color(theme === "dark" ? 0.985 : 0.985, 0.012, INPUTS.textHue); // text on a solid
const dark = color(0.16, 0.014, INPUTS.neutral.hue);
const onSolid = (solid, theme) =>
  wcagContrast(css(ink(theme)), css(solid)) >= wcagContrast(css(dark), css(solid)) ? ink(theme) : dark;

function theme(themeName, contrast) {
  const scales = { neutral: neutralScale(themeName, contrast) };
  scales.accent = chromaticScale(themeName, "accent", INPUTS.accent, contrast);
  for (const [name, spec] of Object.entries(INPUTS.status))
    scales[name] = chromaticScale(themeName, name, spec, contrast);
  const contrasts = Object.fromEntries(
    Object.entries(scales)
      .filter(([name]) => name !== "neutral")
      .map(([name, scale]) => [name, onSolid(scale[8], themeName)]),
  );
  return { scales, contrasts };
}

// ---- contrast rules (WCAG 2.2 AA unless noted) ----------------------------------------------------------------
function validate(label, { scales, contrasts }) {
  const failures = [];
  const ratio = (a, b) => wcagContrast(css(a), css(b));
  const need = (name, actual, minimum) => {
    if (actual < minimum) failures.push(`${label}: ${name} ${actual.toFixed(2)} < ${minimum}`);
  };
  const n = scales.neutral;
  need("text (neutral-12) on app surface (neutral-1)", ratio(n[11], n[0]), 10);
  need("secondary text (neutral-11) on app surface", ratio(n[10], n[0]), 4.5);
  need("secondary text (neutral-11) on card surface (neutral-2)", ratio(n[10], n[1]), 4.5);
  need("form-control border (neutral-9) on app surface [WCAG 1.4.11]", ratio(n[8], n[0]), 3);
  for (const name of ["accent", "success", "warning", "danger", "running"]) {
    const s = scales[name];
    need(`${name} text (step 11) on app surface`, ratio(s[10], n[0]), 4.5);
    need(`${name} text (step 11) on card surface`, ratio(s[10], n[1]), 4.5);
    need(`label on ${name} solid (step 9)`, ratio(contrasts[name], s[8]), 4.5);
  }
  return failures;
}

// ---- output -----------------------------------------------------------------------------------------------
function block(selector, themeName, contrast) {
  const built = theme(themeName, contrast);
  const lines = [`${selector} {`, `  color-scheme: ${themeName};`];
  for (const [name, scale] of Object.entries(built.scales)) {
    for (const [i, step] of scale.entries()) lines.push(`  --${name}-${i + 1}: ${css(step)};`);
  }
  for (const [name, text] of Object.entries(built.contrasts))
    lines.push(`  --${name}-contrast: ${css(text)};`);
  lines.push("}");
  return { text: lines.join("\n"), failures: validate(`${themeName}@${contrast}`, built) };
}

function render() {
  const parts = [
    block(":root,\n:root[data-theme='dark']", "dark", 0.5),
    block(":root[data-theme='light']", "light", 0.5),
    block(":root[data-theme='dark'][data-contrast='high']", "dark", 1),
    block(":root[data-theme='light'][data-contrast='high']", "light", 1),
  ];
  const failures = parts.flatMap((part) => part.failures);
  const header = [
    "/* GENERATED by scripts/generate-tokens.mjs from three inputs (neutral base, accent, contrast) — do not edit. */",
    "/* Radix 12-step roles in OKLCH. Regenerate with `npm run tokens`; `npm run tokens:check` fails on drift. */",
    "",
  ].join("\n");
  return { css: `${header}${parts.map((part) => part.text).join("\n\n")}\n`, failures };
}

const { css: output, failures } = render();
if (failures.length > 0) {
  console.error(`FAIL ${failures.length} contrast rule(s) broken:\n  ${failures.join("\n  ")}`);
  process.exit(1);
}

if (process.argv.includes("--check")) {
  let current = "";
  try {
    current = readFileSync(OUT, "utf8");
  } catch {
    // missing file counts as stale
  }
  if (current !== output) {
    console.error("FAIL tokens.generated.css is stale — run `npm run tokens`");
    process.exit(1);
  }
  console.log("ok  tokens.generated.css is current; every contrast rule holds in 4 themes");
} else {
  writeFileSync(OUT, output);
  console.log(`wrote ${OUT}`);
}
