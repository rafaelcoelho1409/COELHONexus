"use client";

/** Renders markdown: GitHub-flavoured tables and task lists, LaTeX math (KaTeX), syntax highlighting with a
 * copy button, `mermaid` diagrams, ANSI terminal output, callouts and anchored headings.
 *
 * It is safe for model- or crawler-written text: raw HTML is dropped unless `allowHtml` is set, and then it is
 * sanitised first; link and image URLs go through react-markdown's protocol allow-list (no `javascript:`);
 * nothing is evaluated. */

import "katex/dist/katex.min.css";
import "./markdown.css";

import type { Element } from "hast";
import { toString as hastText } from "hast-util-to-string";
import { type ComponentProps, type ReactNode, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeSlug from "rehype-slug";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import type { PluggableList } from "unified";

import { CodeBlock } from "./code-block";
import { normalizeMath } from "./math";
import { MermaidDiagram } from "./mermaid-diagram";
import { remarkAlerts } from "./remark-alerts";
import { TerminalBlock } from "./terminal-block";
import { VegaLiteVisualization } from "./vega-lite-visualization";

/** Fences whose body is not source code to colour. */
const PLAIN_LANGUAGES = ["mermaid", "ansi", "terminal"];

// The default sanitiser keeps only `language-*` classes on code; the math plugin also marks its output with
// `math-inline` / `math-display`, which KaTeX then looks for.
const SANITIZE_SCHEMA = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [["className", /^language-./, "math-inline", "math-display"]],
  },
};

const REMARK_PLUGINS: PluggableList = [remarkGfm, remarkMath, remarkAlerts];
const SLUG_AND_COLOUR: PluggableList = [
  rehypeSlug,
  // `detect`: the chapters' fences carry no language tag, so guess one (as the old UI's highlighter did).
  [rehypeHighlight, { plainText: PLAIN_LANGUAGES, detect: true }],
  rehypeKatex,
];
// Raw HTML is parsed, then sanitised BEFORE anything adds classes or ids of its own.
const REHYPE_PLUGINS: PluggableList = SLUG_AND_COLOUR;
const REHYPE_PLUGINS_WITH_HTML: PluggableList = [
  rehypeRaw,
  [rehypeSanitize, SANITIZE_SCHEMA],
  ...SLUG_AND_COLOUR,
];

function codeChild(pre: Element | undefined): Element | undefined {
  return pre?.children.find(
    (child): child is Element => child.type === "element" && child.tagName === "code",
  );
}

function languageOf(code: Element | undefined): string | null {
  const classes = Array.isArray(code?.properties?.className) ? code.properties.className : [];
  const named = classes.find(
    (name): name is string => typeof name === "string" && name.startsWith("language-"),
  );
  return named ? named.slice("language-".length) : null;
}

const BASE_COMPONENTS: Components = {
  pre({ node, children }) {
    return renderPre(node, children);
  },
  table({ children }) {
    return (
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a wide table scrolls sideways, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
      <div className="md-table" tabIndex={0}>
        <table>{children}</table>
      </div>
    );
  },
  a(props) {
    const { href, children, ...rest } = withoutNode(props);
    const external = href !== undefined && /^https?:/i.test(href);
    return (
      <a href={href} {...rest} {...(external ? { target: "_blank", rel: "noreferrer noopener" } : {})}>
        {children}
      </a>
    );
  },
  img(props) {
    const { alt, ...rest } = withoutNode(props);
    // Arbitrary remote images inside documents, not assets we ship: `next/image` needs known sizes and hosts.
    return (
      // biome-ignore lint/performance/noImgElement: remote document images, see above
      <img alt={alt ?? ""} loading="lazy" {...rest} /> // eslint-disable-line @next/next/no-img-element
    );
  },
};

function renderPre(
  node: Element | undefined,
  children: ReactNode,
  onExpandVisual?: (source: string, language: string, label: string) => void,
) {
  const code = codeChild(node);
  const language = languageOf(code);
  const raw = code ? hastText(code) : "";
  if (language === "mermaid") {
    const firstLine = raw.trimStart().split(/\r?\n/, 1)[0]?.trim().toLowerCase();
    let label = "Diagram";
    if (firstLine?.startsWith("timeline")) label = "Timeline";
    else if (firstLine?.startsWith("xychart")) label = "Chart";
    else if (firstLine?.startsWith("flowchart") || firstLine?.startsWith("graph ")) label = "Flowchart";
    return (
      <MermaidDiagram
        source={raw}
        label={label}
        onExpand={onExpandVisual ? () => onExpandVisual(raw, "mermaid", label) : undefined}
      />
    );
  }
  if (language === "vega-lite") {
    return (
      <VegaLiteVisualization
        source={raw}
        onExpand={onExpandVisual ? (label) => onExpandVisual(raw, "vega-lite", label) : undefined}
      />
    );
  }
  if (language === "ansi" || language === "terminal") return <TerminalBlock source={raw} />;
  return (
    <CodeBlock language={language} raw={raw.replace(/\n$/, "")}>
      {children}
    </CodeBlock>
  );
}

/** react-markdown hands every component its syntax-tree `node`; it must not reach the DOM element. */
function withoutNode<T extends { node?: unknown }>(props: T): Omit<T, "node"> {
  const { node, ...rest } = props;
  void node;
  return rest;
}

export function Markdown({
  text,
  allowHtml = false,
  remarkPlugins,
  components,
  onExpandVisual,
}: {
  text: string;
  /** Parse raw HTML in the text (crawled pages carry tables and the like), sanitised first. */
  allowHtml?: boolean;
  /** Extra remark plugins, applied after the built-in ones. Must be referentially stable. */
  remarkPlugins?: PluggableList;
  /** Overrides for single elements. Must be referentially stable. */
  components?: Components;
  /** Called when a rendered diagram, chart, or map's expand control is used. */
  onExpandVisual?: (source: string, language: string, label: string) => void;
}) {
  const source = useMemo(() => normalizeMath(text), [text]);
  const remark = useMemo(() => [...REMARK_PLUGINS, ...(remarkPlugins ?? [])], [remarkPlugins]);
  const merged = useMemo(() => {
    const base: Components = onExpandVisual
      ? {
          ...BASE_COMPONENTS,
          pre({ node, children }) {
            return renderPre(node, children, onExpandVisual);
          },
        }
      : BASE_COMPONENTS;
    return { ...base, ...components };
  }, [components, onExpandVisual]);
  const options: ComponentProps<typeof ReactMarkdown> = {
    remarkPlugins: remark,
    rehypePlugins: allowHtml ? REHYPE_PLUGINS_WITH_HTML : REHYPE_PLUGINS,
    remarkRehypeOptions: allowHtml ? { allowDangerousHtml: true } : undefined,
    components: merged,
  };
  return (
    <div className="markdown">
      <ReactMarkdown {...options}>{source}</ReactMarkdown>
    </div>
  );
}
