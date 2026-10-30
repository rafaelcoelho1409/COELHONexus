"use client";

/** The query editor on CodeMirror 6: line numbers, JSON or Cypher highlighting, bracket matching, undo history,
 * Mod-Enter to run. Colours come from the design tokens (CSS variables), so the dark, light and high-contrast
 * themes need nothing here. Tab indents; Escape then Tab leaves the editor (CodeMirror's keyboard-trap escape).
 * Loaded lazily through `query-editor.tsx`, so the editor stays out of every other page's JavaScript. */

import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { json } from "@codemirror/lang-json";
import {
  bracketMatching,
  HighlightStyle,
  indentOnInput,
  StreamLanguage,
  syntaxHighlighting,
} from "@codemirror/language";
import { cypher } from "@codemirror/legacy-modes/mode/cypher";
import { Compartment, EditorState, Prec } from "@codemirror/state";
import { EditorView, highlightActiveLine, keymap, lineNumbers } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { useLayoutEffect, useRef } from "react";

import type { QueryLanguage } from "../params";

const THEME = EditorView.theme({
  "&": {
    color: "var(--foreground)",
    backgroundColor: "var(--card)",
    fontSize: "13px",
    // About six lines at rest, growing with the query up to about sixteen before it scrolls: a short Cypher query
    // does not hold a screenful of empty editor, and the result below starts higher up.
    minHeight: "9rem",
    maxHeight: "22rem",
  },
  "&.cm-focused": { outline: "2px solid var(--ring)", outlineOffset: "-2px" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.6", overflow: "auto" },
  ".cm-content": { caretColor: "var(--foreground)", padding: "8px 0" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--foreground)" },
  ".cm-selectionBackground, &.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground": {
    backgroundColor: "var(--accent)",
  },
  ".cm-activeLine": { backgroundColor: "var(--muted)" },
  ".cm-gutters": {
    backgroundColor: "var(--card)",
    color: "var(--muted-foreground)",
    borderRight: "1px solid var(--border)",
  },
  ".cm-activeLineGutter": { backgroundColor: "var(--muted)", color: "var(--foreground)" },
  "&.cm-focused .cm-matchingBracket": {
    backgroundColor: "transparent",
    outline: "1px solid var(--ring)",
  },
});

const HIGHLIGHT = HighlightStyle.define([
  { tag: [tags.keyword, tags.operatorKeyword, tags.definitionKeyword], color: "var(--primary-text)" },
  { tag: [tags.string, tags.special(tags.string)], color: "var(--success-text)" },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], color: "var(--warning-text)" },
  { tag: [tags.propertyName, tags.attributeName, tags.variableName], color: "var(--running-text)" },
  { tag: [tags.typeName, tags.className, tags.labelName], color: "var(--primary-text)" },
  {
    tag: [tags.comment, tags.lineComment, tags.blockComment],
    color: "var(--muted-foreground)",
    fontStyle: "italic",
  },
  { tag: tags.invalid, color: "var(--destructive-text)" },
]);

function languageExtension(language: QueryLanguage) {
  return language === "cypher" ? StreamLanguage.define(cypher) : json();
}

function lockExtension(locked: boolean) {
  return [EditorState.readOnly.of(locked), EditorView.editable.of(!locked)];
}

export type QueryEditorProps = {
  value: string;
  language: QueryLanguage;
  /** The accessible name of the text area. */
  label: string;
  /** The id of the line that explains the Tab and Escape keys. */
  describedBy: string;
  /** True while the assistant is writing: the editor stays visible but takes no typing. */
  locked: boolean;
  onChange: (value: string) => void;
  onRun: () => void;
};

export function QueryEditorCm(props: QueryEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const language = useRef(new Compartment());
  const lock = useRef(new Compartment());
  // Handlers and the first values are read through a ref the editor outlives; written after render, not during.
  const latest = useRef(props);
  useLayoutEffect(() => {
    latest.current = props;
  });

  useLayoutEffect(() => {
    const parent = host.current;
    if (!parent) return;
    const initial = latest.current;
    const editor = new EditorView({
      parent,
      state: EditorState.create({
        doc: initial.value,
        extensions: [
          lineNumbers(),
          // Long lines wrap instead of scrolling sideways: nothing to pan on a phone, and no scroll region for a
          // keyboard user to find.
          EditorView.lineWrapping,
          history(),
          bracketMatching(),
          indentOnInput(),
          highlightActiveLine(),
          syntaxHighlighting(HIGHLIGHT),
          THEME,
          language.current.of(languageExtension(initial.language)),
          lock.current.of(lockExtension(initial.locked)),
          EditorView.contentAttributes.of({
            "aria-label": initial.label,
            "aria-multiline": "true",
            "aria-describedby": initial.describedBy,
          }),
          // Mod-Enter is "insert blank line" in the default keymap; Run must win.
          Prec.highest(
            keymap.of([
              {
                key: "Mod-Enter",
                preventDefault: true,
                run: () => {
                  latest.current.onRun();
                  return true;
                },
              },
            ]),
          ),
          keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) latest.current.onChange(update.state.doc.toString());
          }),
        ],
      }),
    });
    view.current = editor;
    return () => {
      editor.destroy();
      view.current = null;
    };
  }, []);

  useLayoutEffect(() => {
    view.current?.dispatch({ effects: language.current.reconfigure(languageExtension(props.language)) });
  }, [props.language]);

  useLayoutEffect(() => {
    view.current?.dispatch({ effects: lock.current.reconfigure(lockExtension(props.locked)) });
  }, [props.locked]);

  // A value that arrives from outside (the assistant, a restored query, a backend switch) replaces the document;
  // the editor's own edits already match, so typing never dispatches.
  useLayoutEffect(() => {
    const editor = view.current;
    if (!editor || editor.state.doc.toString() === props.value) return;
    editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: props.value } });
  }, [props.value]);

  return (
    <div ref={host} data-testid="query-editor" className="overflow-hidden rounded-sm border border-border" />
  );
}
