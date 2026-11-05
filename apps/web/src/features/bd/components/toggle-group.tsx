import { ActionButton } from "./action-button";

/** A "show it as…" choice: one `ActionButton` per option, the chosen one marked `aria-pressed` (and drawn with the
 * pressed look), so the state is in the markup and never only in colour. */
export function ToggleGroup<Id extends string>({
  legend,
  options,
  value,
  onChange,
}: {
  legend: string;
  options: readonly { id: Id; label: string }[];
  value: Id;
  onChange: (id: Id) => void;
}) {
  return (
    <fieldset className="m-0 flex flex-wrap items-center gap-1.5 border-0 p-0">
      <legend className="sr-only">{legend}</legend>
      {options.map((option) => (
        <ActionButton key={option.id} aria-pressed={value === option.id} onClick={() => onChange(option.id)}>
          {option.label}
        </ActionButton>
      ))}
    </fieldset>
  );
}
