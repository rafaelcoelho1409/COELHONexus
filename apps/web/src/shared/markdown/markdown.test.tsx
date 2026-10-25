import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Markdown } from "./markdown";
import { markdownToc } from "./toc";

vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async (_id: string, source: string) => {
      if (source.includes("BROKEN")) throw new Error("parse error");
      return { svg: `<svg data-testid="drawn" data-source="${source.length}"></svg>` };
    }),
  },
}));

const ESC = String.fromCharCode(27);

describe("Markdown", () => {
  it("gives headings the ids the outline uses, repeated ones included", () => {
    const text = "## Setup\n\n### Details\n\n## Setup\n";
    render(<Markdown text={text} />);
    const ids = screen.getAllByRole("heading").map((heading) => heading.id);
    expect(ids).toEqual(markdownToc(text).map((entry) => entry.id));
    expect(ids).toEqual(["setup", "details", "setup-1"]);
  });

  it("renders GitHub tables inside a focusable scroll wrapper, and task lists", () => {
    render(<Markdown text={"| a | b |\n| - | - |\n| 1 | 2 |\n\n- [x] done\n- [ ] todo\n"} />);
    const table = screen.getByRole("table");
    expect(table.parentElement).toHaveAttribute("tabindex", "0");
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  });

  it("highlights code, labels the language and copies the source", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<Markdown text={"```js\nconst answer = 42;\n```\n"} />);

    expect(screen.getByText("js")).toBeInTheDocument();
    expect(screen.getByText("const")).toHaveClass("hljs-keyword");
    await userEvent.click(screen.getByRole("button", { name: "Copy code" }));
    expect(writeText).toHaveBeenCalledWith("const answer = 42;");
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("guesses the language of a fence that has none, as the synthesized chapters write them", () => {
    const code =
      'from deepagents import create_deep_agent\n\ndef get_weather(city: str) -> str:\n    return f"sunny in {city}"\n';
    render(<Markdown text={`\`\`\`\n${code}\`\`\`\n`} />);

    expect(screen.getByText("python")).toBeInTheDocument();
    expect(screen.getByText("def")).toHaveClass("hljs-keyword");
  });

  it("makes a code block focusable so a long line can be scrolled by keyboard", () => {
    render(<Markdown text={"```\nplain\n```\n"} />);
    expect(screen.getByText("plain").closest("pre")).toHaveAttribute("tabindex", "0");
  });

  it("drops the target of a javascript: link and opens real ones safely", () => {
    render(
      <Markdown text="[bad](javascript:alert(1)) [good](https://example.com/a) [here](#top) [rel](/dd/study)" />,
    );
    expect(screen.getByText("bad").closest("a")).not.toHaveAttribute(
      "href",
      expect.stringContaining("javascript"),
    );
    const good = screen.getByRole("link", { name: "good" });
    expect(good).toHaveAttribute("target", "_blank");
    expect(good).toHaveAttribute("rel", "noreferrer noopener");
    expect(screen.getByRole("link", { name: "here" })).not.toHaveAttribute("target");
    expect(screen.getByRole("link", { name: "rel" })).toHaveAttribute("href", "/dd/study");
  });

  it("does not render raw HTML by default", () => {
    const { container } = render(<Markdown text={"<script>window.hacked = 1</script><b>bold?</b> text"} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(Reflect.get(window, "hacked")).toBeUndefined();
  });

  it("sanitises raw HTML when it is allowed: tables stay, scripts and handlers go", () => {
    const html =
      '<table><tbody><tr><td>cell</td></tr></tbody></table>\n\n<img src="x" onerror="window.hacked = 1">\n\n<script>window.hacked = 1</script>';
    const { container } = render(<Markdown text={html} allowHtml />);
    expect(within(container).getByRole("cell", { name: "cell" })).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(Reflect.get(window, "hacked")).toBeUndefined();
  });

  it("renders inline and display math, including LaTeX written without dollar fences", () => {
    const { container } = render(
      <Markdown
        text={"Inline $x^2$ and \\(y\\), then\n\n\\[ z = 1 \\]\n\n\\begin{align}a &= b\\end{align}\n"}
      />,
    );
    expect(container.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(4);
    expect(container.querySelectorAll(".katex-display").length).toBe(2);
    // KaTeX would print an unknown `align` as red source text; the renamed `aligned` rendered fine
    expect(container.querySelector(".katex-error")).toBeNull();
    expect(container.textContent).not.toContain("\\begin{align}");
  });

  it("turns a [!NOTE] blockquote into a titled callout", () => {
    render(<Markdown text={"> [!WARNING]\n> Mind the gap.\n"} />);
    const aside = screen.getByText("Mind the gap.").closest("aside");
    expect(aside).toHaveClass("md-alert", "md-alert-warning");
    expect(within(aside as HTMLElement).getByText("Warning")).toBeInTheDocument();
  });

  it("colours ANSI terminal output without running anything", () => {
    render(<Markdown text={`\`\`\`ansi\nok ${ESC}[32mPASS${ESC}[0m\n\`\`\`\n`} />);
    expect(screen.getByLabelText("Terminal output")).toBeInTheDocument();
    expect(screen.getByText("PASS")).toHaveClass("ansi-green");
  });

  it("draws a mermaid diagram once the library has loaded, and shows the source when it cannot", async () => {
    render(<Markdown text={"```mermaid\ngraph TD; A-->B\n```\n\n```mermaid\nBROKEN\n```\n"} />);
    expect(await screen.findByTestId("drawn")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/could not be drawn/)).toBeInTheDocument());
    expect(screen.getByText("BROKEN")).toBeInTheDocument();
  });

  it("lets a caller override single elements", () => {
    render(<Markdown text="`code`" components={{ code: ({ children }) => <kbd>{children}</kbd> }} />);
    expect(screen.getByText("code").tagName).toBe("KBD");
  });
});
