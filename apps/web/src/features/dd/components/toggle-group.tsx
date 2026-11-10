import { ActionButton } from "./action-button";

/** A compact segmented toggle: one connected control, with its selected value exposed through `aria-pressed`. */
export function ToggleGroup<Id extends string>({
  legend,
  options,
  value,
  onChange,
  fullWidth = false,
}: {
  legend: string;
  options: readonly { id: Id; label: string }[];
  value: Id;
  onChange: (id: Id) => void;
  /** Stretch the control and divide its options evenly across its width. */
  fullWidth?: boolean;
}) {
  return (
    <fieldset
      className={`m-0 ${fullWidth ? "flex w-full" : "inline-flex"} overflow-hidden rounded-sm border border-border bg-background p-0`}
    >
      <legend className="sr-only">{legend}</legend>
      {options.map((option) => (
        <ActionButton
          key={option.id}
          aria-pressed={value === option.id}
          onClick={() => onChange(option.id)}
          className={`min-h-9 rounded-none border-0 border-r border-r-border last:border-r-0 ${
            fullWidth ? "min-w-0 flex-1 justify-center whitespace-nowrap px-1.5" : "px-2.5"
          }`}
        >
          {option.label}
        </ActionButton>
      ))}
    </fieldset>
  );
}
