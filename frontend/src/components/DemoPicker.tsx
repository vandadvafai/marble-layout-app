// Small dropdown for switching between demo layouts. Foundation
// scope only — no search, no thumbnail, no recent list. A real
// project picker will replace this once the editing milestone
// lands.

import type { DemoIndexEntry } from "../lib/types";

interface Props {
  demos: DemoIndexEntry[];
  value: string;
  onChange: (demoId: string) => void;
  disabled?: boolean;
}

export default function DemoPicker(
  { demos, value, onChange, disabled }: Props,
) {
  return (
    <label className="demo-picker">
      <span>Demo</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        {demos.map((d) => (
          <option key={d.demo_id} value={d.demo_id}>
            {d.label}
          </option>
        ))}
      </select>
    </label>
  );
}
