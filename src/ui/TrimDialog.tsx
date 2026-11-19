import { useEffect, useEffectEvent, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import type { Snapshot } from "./App";
import { Icon, Slider } from "./Controls";
import WheelCanvas from "./WheelCanvas";

type TrimOpen = {
  id: number;
  duration: number;
  rolling: boolean;
  wheel: number;
  slot: number;
  label: string;
  gain: number;
};

type TrimPeaks = { id: number; peaks: number[] };
type TrimProgress = { playing: boolean; seconds: number | null };

type Props = {
  snapshot: Snapshot;
  run: (action: Record<string, unknown>) => Promise<unknown>;
};

type Session = TrimOpen & { peaks: number[]; playing: boolean; seconds: number | null };

const MIN_RANGE = 0.001;
const WAVE_HEIGHT = 148;

export default function TrimDialog({ snapshot, run }: Props) {
  const [trim, setTrim] = useState<Session | null>(null);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  const [label, setLabel] = useState("");
  const [gain, setGain] = useState(100);
  const [wheel, setWheel] = useState(0);
  const [slot, setSlot] = useState(0);
  const [saving, setSaving] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const drag = useRef<Drag | null>(null);

  useEffect(() => {
    const stops = [
      listen<TrimOpen>("wordy://trim-open", (event) => {
        const next = { ...event.payload, peaks: [], playing: false, seconds: null };
        setTrim(next);
        setStart(0);
        setEnd(event.payload.duration);
        setLabel(event.payload.label);
        setGain(event.payload.gain);
        setWheel(event.payload.wheel);
        setSlot(event.payload.slot);
      }),
      listen<TrimPeaks>("wordy://trim-peaks", (event) =>
        setTrim((current) =>
          current && current.id === event.payload.id
            ? { ...current, peaks: event.payload.peaks }
            : current,
        ),
      ),
      listen<TrimProgress>("wordy://trim-progress", (event) =>
        setTrim((current) =>
          current
            ? {
                ...current,
                playing: event.payload.playing,
                seconds: event.payload.seconds,
              }
            : current,
        ),
      ),
      listen("wordy://trim-closed", () => {
        setTrim(null);
        setSaving(false);
        drag.current = null;
      }),
      listen("wordy://trim-save-failed", () => setSaving(false)),
    ];
    return () => {
      stops.forEach((stop) => stop.then((off) => off()));
    };
  }, []);

  useEffect(() => {
    if (!trim) return;
    const onKey = (event: KeyboardEvent) => {
      // 输入框里的空格是文字，不能触发试听。
      if (isEditable(event.target)) return;
      if (event.code === "Space") {
        event.preventDefault();
        previewOrStop(trim, start, end, gain, run);
      } else if (event.key === "Escape") {
        run({ type: "trim_cancel", id: trim.id });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [trim, start, end, gain, run]);

  const redrawWave = useEffectEvent(() => {
    const element = canvas.current;
    if (!element || !trim) return;
    drawWave(
      element,
      trim.peaks,
      trim.duration,
      start,
      end,
      trim.seconds === null ? null : start + trim.seconds,
    );
  });

  useEffect(() => {
    redrawWave();
  }, [trim?.id, trim?.peaks, trim?.duration, trim?.seconds, start, end]);

  const trimOpen = trim !== null;
  useEffect(() => {
    const element = canvas.current;
    if (!trimOpen || !element) return;
    // 拖选和试听进度只重绘，画布大小则按设备像素观察。
    const observer = new ResizeObserver(() => redrawWave());
    observer.observe(element, { box: "device-pixel-content-box" });
    return () => observer.disconnect();
  }, [trimOpen]);

  if (!trim) return null;

  const duration = trim.duration;
  const wheelItems = [
    {
      name: snapshot.settings.chat_wheel_name,
      labels: snapshot.settings.chat_wheel_labels,
      texts: snapshot.settings.chat_wheel_texts,
      slots: snapshot.settings.chat_wheel_slots,
    },
    ...snapshot.settings.additional_chat_wheels,
  ];
  const targetWheel = wheelItems[Math.min(wheel, wheelItems.length - 1)];
  const targetLabels = targetWheel
    ? Array.from({ length: 8 }, (_, index) =>
        targetWheel.labels[index] || targetWheel.texts[index] || (targetWheel.slots[index] ? "音频" : ""),
      )
    : [];
  const wheels = wheelItems.map((item, order) => item.name || `轮盘 ${order + 1}`);
  const timeAt = (clientX: number) => {
    const element = canvas.current;
    if (!element) return 0;
    const rect = element.getBoundingClientRect();
    return clamp(((clientX - rect.left) / rect.width) * duration, 0, duration);
  };
  const beginDrag = (event: React.PointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    const time = timeAt(event.clientX);
    const element = canvas.current;
    const width = element?.getBoundingClientRect().width ?? 1;
    const threshold = (8 / width) * duration;
    if (trim.playing) run({ type: "trim_stop", id: trim.id });
    if (Math.abs(time - start) <= threshold) {
      drag.current = { mode: "start" };
    } else if (Math.abs(time - end) <= threshold) {
      drag.current = { mode: "end" };
    } else {
      // 任意方向拖选：从按下的位置向左右都可以拉出新区间。
      drag.current = { mode: "new", anchor: time };
      setStart(time);
      setEnd(Math.min(duration, time + MIN_RANGE));
    }
  };
  const moveDrag = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const active = drag.current;
    if (!active) return;
    const time = timeAt(event.clientX);
    if (active.mode === "start") {
      setStart(Math.min(time, end - MIN_RANGE));
    } else if (active.mode === "end") {
      setEnd(Math.max(time, start + MIN_RANGE));
    } else {
      const from = Math.min(active.anchor, time);
      const to = Math.max(active.anchor, time);
      setStart(clamp(from, 0, duration - MIN_RANGE));
      setEnd(clamp(Math.max(to, from + MIN_RANGE), MIN_RANGE, duration));
    }
  };
  const endDrag = () => {
    drag.current = null;
  };

  return (
    <div className="overlay" role="dialog" aria-label={trim.rolling ? "保存后台录制" : "截取录音"}>
      <section className="overlay-card panel-in flex h-[calc(100%-2rem)] w-[calc(100%-2rem)] max-w-[760px] flex-col overflow-y-auto p-4">
        <header className="panel-head">
          <h2 className="panel-title">{trim.rolling ? "保存后台录制" : "截取录音"}</h2>
          <button
            className="btn icon-btn"
            title="取消"
            aria-label="取消截取"
            onClick={() => run({ type: "trim_cancel", id: trim.id })}
          >
            <Icon name="close" />
          </button>
        </header>

        <div className="relative overflow-hidden rounded-lg border border-[var(--line-soft)] bg-[var(--field)]">
        <canvas
          ref={canvas}
          className="block h-[148px] w-full cursor-crosshair touch-none"
          onPointerDown={beginDrag}
          onPointerMove={moveDrag}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        />
        {trim.peaks.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-[var(--muted)]">
            正在加载波形…
          </div>
        )}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <label className="text-sm">
          开始（秒）
          <input
            className="field mt-1"
            type="number"
            min={0}
            max={duration}
            step={0.001}
            value={round(start)}
            onChange={(event) => setStart(clamp(Number(event.target.value), 0, end - MIN_RANGE))}
          />
        </label>
        <label className="text-sm">
          结束（秒）
          <input
            className="field mt-1"
            type="number"
            min={0}
            max={duration}
            step={0.001}
            value={round(end)}
            onChange={(event) =>
              setEnd(clamp(Number(event.target.value), start + MIN_RANGE, duration))
            }
          />
        </label>
      </div>

      <p className="mt-2 text-xs text-[var(--muted)]">
        拖动两端手柄调整范围，或在波形上拖选；空格试听 / 停止。
      </p>

      <div className="mt-2 flex flex-wrap items-end gap-3">
        <label className="min-w-[9rem] flex-1 text-sm">
          名称
          <input
            className="field mt-1"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </label>
        <div className="w-44">
          <Slider
            label="音量"
            value={Math.round(gain)}
            min={0}
            max={300}
            step={5}
            display={`${Math.round(gain)}%`}
            onChange={setGain}
          />
        </div>
      </div>

      <div className="mt-3 flex gap-3">
        <div className="relative aspect-[634/540] w-full max-w-[420px] shrink-0 overflow-hidden rounded-lg border border-[var(--line-soft)] bg-[var(--field)]">
          <WheelCanvas
            labels={targetLabels}
            font={snapshot.settings.ui_font}
            selected={slot}
            onSelectSlot={trim.rolling ? setSlot : undefined}
            className="h-full w-full"
          />
        </div>
        <div className="min-w-0 flex-1">
          {trim.rolling ? (
            <>
              <label className="block text-sm">
                保存到轮盘
                <select
                  className="field mt-1"
                  value={wheel}
                  onChange={(event) => setWheel(Number(event.target.value))}
                >
                  {wheels.map((name, order) => (
                    <option key={order} value={order}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
              <p className="mt-2 text-xs text-[var(--muted)]">
                点击左侧轮盘扇区选择槽位 · 当前：第 {slot + 1} 槽
                {targetWheel ? `（${slotName(targetWheel, slot)}）` : ""}
              </p>
              <button
                className="btn icon-btn mt-2"
                title="新增轮盘"
                aria-label="新增轮盘"
                onClick={() => {
                  setWheel(wheelItems.length);
                  run({ type: "add_wheel" });
                }}
              >
                <Icon name="plus" />
              </button>
            </>
          ) : (
            <p className="text-sm">
              保存到：<span className="text-[var(--text)]">{wheels[wheel] ?? wheels[0]}</span>
              <span className="text-[var(--muted)]">
                {" "}
                · 第 {slot + 1} 槽{targetWheel ? `（${slotName(targetWheel, slot)}）` : ""}
              </span>
            </p>
          )}
        </div>
      </div>

      <footer className="mt-auto flex items-center justify-between pt-3">
        <span className="readout text-xs">
          已选 {round(end - start)} 秒 / 共 {round(duration)} 秒
        </span>
        <div className="flex gap-2">
          <button className="btn" onClick={() => previewOrStop(trim, start, end, gain, run)}>
            {trim.playing ? "停止试听" : "试听片段"}
          </button>
          <button
            className="btn primary"
            disabled={saving}
            onClick={() => {
              setSaving(true);
              // 原生拒绝（超时、无效槽位等）时不会发关闭事件，按回复复位按钮。
              run({
                type: "trim_save",
                id: trim.id,
                start,
                end,
                label,
                gain,
                wheel,
                slot,
              }).catch(() => setSaving(false));
            }}
          >
            保存片段
          </button>
        </div>
      </footer>
      </section>
    </div>
  );
}

type Drag =
  | { mode: "start" }
  | { mode: "end" }
  | { mode: "new"; anchor: number }
;

const clamp = (value: number, min: number, max: number) =>
  Number.isFinite(value) ? Math.min(Math.max(value, min), max) : min;

/// 文本类控件（含下拉）获得焦点时，快捷键让位给正常输入。
const isEditable = (target: EventTarget | null) =>
  target instanceof HTMLElement &&
  (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

const round = (value: number) => Math.round(value * 1000) / 1000;

function slotName(
  wheel: { labels: string[]; texts: string[]; slots: string[] },
  index: number,
) {
  return wheel.labels[index] || wheel.texts[index] || (wheel.slots[index] ? "音频" : `槽 ${index + 1}`);
}

function previewOrStop(
  trim: Session,
  start: number,
  end: number,
  gain: number,
  run: (action: Record<string, unknown>) => void,
) {
  run(
    trim.playing
      ? { type: "trim_stop", id: trim.id }
      : { type: "trim_preview", id: trim.id, start, end, gain },
  );
}

function drawWave(
  canvas: HTMLCanvasElement,
  peaks: number[],
  duration: number,
  start: number,
  end: number,
  seconds: number | null,
) {
  const width = canvas.clientWidth;
  const height = WAVE_HEIGHT;
  const ratio = window.devicePixelRatio || 1;
  const pixelWidth = Math.max(1, Math.round(width * ratio));
  const pixelHeight = Math.round(height * ratio);
  // 给 Canvas 宽高赋相同的值也会重置位图和绘图状态。
  if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
  if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
  const context = canvas.getContext("2d");
  if (!context) return;
  context.resetTransform();
  context.clearRect(0, 0, pixelWidth, pixelHeight);
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.fillStyle = "#0f1211";
  context.fillRect(0, 0, width, height);
  if (peaks.length === 0) return;

  const middle = height / 2;
  const x = (value: number) => (value / Math.max(duration, MIN_RANGE)) * width;
  for (let column = 0; column < width; column++) {
    const from = Math.floor((column / width) * peaks.length);
    const to = Math.max(from + 1, Math.floor(((column + 1) / width) * peaks.length));
    let peak = 0;
    for (let index = from; index < to; index++) peak = Math.max(peak, peaks[index] ?? 0);
    const bar = Math.max(1, peak * (middle - 4));
    const selected = column >= x(start) && column <= x(end);
    context.fillStyle = selected ? "#e3a94b" : "#3a423c";
    context.fillRect(column, middle - bar, 1, bar * 2);
  }

  context.fillStyle = "rgba(227, 169, 75, 0.12)";
  context.fillRect(x(start), 0, Math.max(1, x(end) - x(start)), height);
  context.fillStyle = "rgba(227, 169, 75, 0.9)";
  context.fillRect(x(start), 0, 1.5, height);
  context.fillRect(x(end) - 1.5, 0, 1.5, height);

  if (seconds !== null) {
    const cursor = x(clamp(seconds, 0, duration));
    context.fillStyle = "#f0bd68";
    context.fillRect(cursor, 0, 1.5, height);
  }
}
