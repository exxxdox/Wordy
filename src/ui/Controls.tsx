import { useEffect, useId, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

export function Panel({
  title,
  icon,
  children,
}: { title: string; icon: IconName; children: ReactNode }) {
  return (
    <section className="panel">
      <header className="panel-head">
        <Icon name={icon} size={20} />
        <h2 className="panel-title">{title}</h2>
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function Row({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor?: string;
  hint?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="setting-row">
      <div className="setting-copy">
        {htmlFor ? <label htmlFor={htmlFor}>{label}</label> : <span>{label}</span>}
        {hint && <p className="hint">{hint}</p>}
      </div>
      {children && <div className="setting-control">{children}</div>}
    </div>
  );
}

type SliderProps = {
  label: string;
  hint?: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display: string;
  disabled?: boolean;
  onChange: (value: number) => void;
};

export function Slider({ label, hint, value, min, max, step, display, disabled, onChange }: SliderProps) {
  const id = useId();
  const percent = max > min ? ((value - min) / (max - min)) * 100 : 0;
  // 填充用 CSS 变量传给轨道；轨道伪元素继承，无需 JS 计算像素。
  const style = { "--fill": `${percent}%` } as CSSProperties;
  return (
    <Row label={label} htmlFor={id} hint={hint}>
      <div className="slider">
        <input
          id={id}
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
        <output htmlFor={id} className="readout">
          {display}
        </output>
      </div>
    </Row>
  );
}

const ICONS = {
  brand: <path d="M2 7v2m3-4v6m3-8v10m3-8v6m3-4v2" />,
  local: (
    <>
      <rect x="2" y="3.5" width="12" height="9" rx="1.5" />
      <path d="M5 6.5h.25m2.5 0H8m2.5 0h.25M5 9.5h6" />
    </>
  ),
  audio: <path d="M2 6h2.5L8 3v10L4.5 10H2Zm8.5-.5a3.5 3.5 0 0 1 0 5M12.5 3.5a6.5 6.5 0 0 1 0 9" />,
  tts: <path d="M2 4V2.5h8V4M6 3v10.5m-2 0h4m1-6V6h5v1.5M11.5 6v7.5" />,
  wheels: (
    <>
      <circle cx="8" cy="8" r="6" />
      <circle cx="8" cy="8" r="1.5" />
      <path d="M2.5 6.5 6.5 8m3 0 4-1.5M8 9.5V14" />
    </>
  ),
  rolling: (
    <>
      <circle cx="8" cy="8" r="6" />
      <circle cx="8" cy="8" r="2.5" fill="currentColor" stroke="none" />
    </>
  ),
  display: <path d="M2 3h12v8H2Zm6 8v2.5m-3 0h6" />,
  mic: (
    <>
      <rect x="6" y="2" width="4" height="8" rx="2" />
      <path d="M3.5 7.5a4.5 4.5 0 0 0 9 0M8 12v2m-2 0h4" />
    </>
  ),
  key: (
    <>
      <circle cx="10.5" cy="5.5" r="3.5" />
      <path d="m8 8-5 5H1.5v-2l5-5m-3 4 1.5 1.5" />
    </>
  ),
  log: <path d="M4 2h5l3 3v9H4Zm5 0v3h3M6 8h4m-4 3h4" />,
  import: <path d="M8 2v8m-3-3 3 3 3-3m-8 4v3h10v-3" />,
  export: <path d="M8 10V2M5 5l3-3 3 3m-8 6v3h10v-3" />,
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

/// 输入框编辑期间不被快照回写覆盖；失焦或提交后以后端值为准。
export function useSyncedInput(initial: string, commit: (value: string) => void) {
  const [draft, setDraft] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  const timer = useRef(0);

  useEffect(() => {
    if (document.activeElement !== ref.current) setDraft(initial);
  }, [initial]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  return {
    ref,
    draft,
    onChange(value: string) {
      setDraft(value);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => commit(value), 300);
    },
    flush() {
      window.clearTimeout(timer.current);
      setDraft((value) => {
        if (value !== initial) commit(value);
        return value;
      });
    },
  };
}
