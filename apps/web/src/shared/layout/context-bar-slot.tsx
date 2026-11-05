/** Where a page's own bar is drawn: right under the section bar and outside `<main>`, so it spans the screen like the
 * two bars above it. The page fills it through `ContextBar`. */
export const CONTEXT_BAR_SLOT_ID = "context-bar";

export function ContextBarSlot() {
  return <div id={CONTEXT_BAR_SLOT_ID} />;
}
