import type { CSSProperties, ReactNode } from "react";

export function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="panel">
      <header className="panel-head">
        <h2 className="panel-title">{title}</h2>
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function Row({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <div>
      {label && <div className="field-row-label">{label}</div>}
      {children}
    </div>
  );
}

export function Hint({ children }: { children: ReactNode }) {
  return <p className="hint">{children}</p>;
}

type SliderProps = {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display: string;
  disabled?: boolean;
  onChange: (value: number) => void;
};

export function Slider({ label, value, min, max, step, display, disabled, onChange }: SliderProps) {
  const percent = max > min ? ((value - min) / (max - min)) * 100 : 0;
  // 填充用 CSS 变量传给轨道；轨道伪元素继承，无需 JS 计算像素。
  const style = { "--fill": `${percent}%` } as CSSProperties;
  return (
    <div className="slider">
      <div className="slider-head">
        <span>{label}</span>
        <output className="readout">{display}</output>
      </div>
      <input
        type="range"
        className="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        style={style}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  );
}

const ICONS = {
  play: <path d="M5.5 3.6 12.5 8l-7 4.4Z" fill="currentColor" stroke="none" />,
  stop: <rect x="5" y="5" width="6" height="6" rx="1" fill="currentColor" stroke="none" />,
  plus: <path d="M8 3.6v8.8M3.6 8h8.8" />,
  trash: <path d="M3.6 5h8.8M6.4 5V3.6h3.2V5M4.8 5l.5 7.4h5.4L11.2 5" />,
  refresh: <path d="M12.8 8A4.8 4.8 0 1 1 11 4.2M12.8 3.6v2.8h-2.8" />,
  close: <path d="m4.4 4.4 7.2 7.2M11.6 4.4l-7.2 7.2" />,
} as const;

export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICONS[name]}
    </svg>
  );
}

export const displayHotkey = (value: string) =>
  value
    .split("+")
    .map((part) =>
      part.length === 1 ? part.toUpperCase() : part.charAt(0).toUpperCase() + part.slice(1),
    )
    .join(" + ");
