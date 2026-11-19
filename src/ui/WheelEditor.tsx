import { useEffect, useRef, useState } from "react";
import type { Snapshot, Wheel } from "./App";
import { displayHotkey } from "./App";
import WheelCanvas from "./WheelCanvas";

const STYLE_OPTIONS = [
  { value: "classic", label: "经典玻璃" },
  { value: "glass", label: "轻薄玻璃" },
  { value: "petals", label: "悬浮花瓣" },
  { value: "game", label: "竞技轮盘" },
];


type Props = {
  snapshot: Snapshot;
  run: (action: Record<string, unknown>) => void;
};

function wheelsOf(snapshot: Snapshot): Wheel[] {
  const settings = snapshot.settings;
  return [
    {
      name: settings.chat_wheel_name,
      hotkey: settings.chat_wheel_hotkey,
      slots: settings.chat_wheel_slots,
      labels: settings.chat_wheel_labels,
      texts: settings.chat_wheel_texts,
      volumes: settings.chat_wheel_volumes,
    },
    ...settings.additional_chat_wheels,
  ];
}

function slotTitle(wheel: Wheel, slot: number) {
  if (wheel.labels[slot]) return wheel.labels[slot];
  if (wheel.texts[slot]) return wheel.texts[slot];
  if (wheel.slots[slot]) return "音频";
  return `槽 ${slot + 1}`;
}

function wheelLabels(wheel: Wheel) {
  return Array.from({ length: 8 }, (_, index) => {
    if (!wheel.slots[index] && !wheel.texts[index]) return "";
    return wheel.labels[index] || wheel.texts[index] || wheel.slots[index];
  });
}

export default function WheelEditor({ snapshot, run }: Props) {
  const settings = snapshot.settings;
  const wheels = wheelsOf(snapshot);
  const index = Math.min(settings.chat_wheel_last_index, wheels.length - 1);
  const wheel = wheels[index] ?? wheels[0];
  const [slot, setSlot] = useState(0);
  const [emptyTextMode, setEmptyTextMode] = useState(false);
  const [gain, setGain] = useState(Math.round((wheel?.volumes[slot] ?? 1) * 100));

  const hasAudio = Boolean(wheel?.slots[slot]);
  const hasText = Boolean(wheel?.texts[slot]);
  const textMode = hasText || (hasAudio === false && emptyTextMode);
  const locked = snapshot.locked;
  const playing = snapshot.preview?.wheel === index && snapshot.preview?.slot === slot;

  const name = useSyncedInput(wheel?.name ?? "", (value) =>
    run({ type: "set_wheel_name", index, name: value }),
  );
  const label = useSyncedInput(wheel?.labels[slot] ?? "", (value) =>
    run({ type: "set_slot_label", index, slot, value }),
  );
  const text = useSyncedInput(wheel?.texts[slot] ?? "", (value) =>
    run({ type: "set_slot_text", index, slot, value }),
  );

  useEffect(() => {
    setEmptyTextMode(false);
  }, [index, slot]);

  useEffect(() => {
    setGain(Math.round((wheel?.volumes[slot] ?? 1) * 100));
  }, [wheel?.volumes[slot]]);

  if (!wheel) return null;

  return (
    <>
      <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
        <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-wider text-[var(--muted)]">
          轮盘与快捷键
        </h2>
        <div className="space-y-3">
          <div className="flex gap-2">
            <select
              className="field min-w-0 flex-1"
              value={index}
              disabled={locked}
              onChange={(event) => run({ type: "select_wheel", index: Number(event.target.value) })}
            >
              {wheels.map((item, order) => (
                <option key={order} value={order}>
                  {item.name || `轮盘 ${order + 1}`}
                </option>
              ))}
            </select>
            <button
              className="btn icon-btn"
              title="新增轮盘"
              aria-label="新增轮盘"
              disabled={locked}
              onClick={() => run({ type: "add_wheel" })}
            >
              ➕
            </button>
            <button
              className="btn icon-btn"
              title="删除轮盘"
              aria-label="删除轮盘"
              disabled={locked || wheels.length <= 1}
              onClick={() => run({ type: "remove_wheel", index })}
            >
              🗑️
            </button>
          </div>
          <div className="flex gap-2">
            <output className="field min-w-0 flex-1 truncate" title="当前快捷键">
              {displayHotkey(wheel.hotkey) || "未绑定快捷键"}
            </output>
            <button
              className="btn icon-btn"
              title="录制快捷键"
              aria-label="录制快捷键"
              disabled={locked}
              onClick={() => run({ type: "record_wheel_hotkey", index })}
            >
              ⌨️
            </button>
          </div>
          <label className="block text-[13px]">
            名称
            <input
              ref={name.ref}
              className="field mt-1.5"
              value={name.draft}
              disabled={locked}
              onChange={(event) => name.onChange(event.target.value)}
              onBlur={name.flush}
            />
          </label>
          <div>
            <div className="mb-1.5 text-[13px]">槽位</div>
            <p className="text-xs text-[var(--muted)]">点击下方轮盘扇区选择槽位</p>
          </div>
          <div className="rounded-xl border border-white/10 bg-black/20 p-2">
            <div className="relative mx-auto aspect-[634/540] w-full max-w-[420px]">
              <WheelCanvas
                labels={wheelLabels(wheel)}
                style={settings.chat_wheel_style}
                selected={slot}
                onSelectSlot={setSlot}
                className="absolute inset-0 h-full w-full cursor-pointer"
              />
            </div>
          </div>
          <p className="text-sm text-[var(--muted)]">
            点击扇区选择槽位 · 当前：<span className="text-[var(--text)]">{slotTitle(wheel, slot)}</span>
          </p>
        </div>
      </section>

      <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
        <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-wider text-[var(--muted)]">
          槽位内容 · 第 {slot + 1} 槽
        </h2>
        <div className="space-y-3">
          <label className="block text-[13px]">
            样式
            <select
              className="field mt-1.5"
              value={settings.chat_wheel_style}
              disabled={locked}
              onChange={(event) => run({ type: "set_wheel_style", style: event.target.value })}
            >
              {STYLE_OPTIONS.map((style) => (
                <option key={style.value} value={style.value}>
                  {style.label}
                </option>
              ))}
            </select>
          </label>
          <div>
            <div className="mb-1.5 text-[13px]">内容类型</div>
            <div className="flex gap-2">
              <button
                className={`btn flex-1 ${!textMode ? "border-[var(--accent)] bg-[var(--accent)]/20" : ""}`}
                title="音频槽"
                aria-label="音频槽"
                disabled={locked}
                onClick={() => {
                  setEmptyTextMode(false);
                  if (hasText) run({ type: "set_slot_mode", index, slot, text: false });
                }}
              >
                音频
              </button>
              <button
                className={`btn flex-1 ${textMode ? "border-[var(--accent)] bg-[var(--accent)]/20" : ""}`}
                title="文字槽"
                aria-label="文字槽"
                disabled={locked}
                onClick={() => {
                  setEmptyTextMode(true);
                  if (hasAudio) run({ type: "set_slot_mode", index, slot, text: true });
                }}
              >
                文字
              </button>
            </div>
            <p className="mt-1 text-xs text-[var(--muted)]">
              {hasAudio
                ? wheel.slots[slot]
                : hasText
                  ? "当前槽以文字朗读"
                  : "选择音频或输入朗读文字"}
            </p>
          </div>
          {!textMode && (
            <div>
              <div className="mb-1.5 flex items-center justify-between text-[13px]">
                <span>音量</span>
                <span className="text-[var(--muted)]">{gain}%</span>
              </div>
              <input
                type="range"
                className="range"
                min={0}
                max={300}
                step={5}
                value={gain}
                disabled={locked || !hasAudio}
                onChange={(event) => {
                  const value = Number(event.target.value);
                  setGain(value);
                  run({ type: "set_slot_volume", index, slot, value });
                }}
              />
            </div>
          )}
          <label className="block text-[13px]">
            名称
            <input
              ref={label.ref}
              className="field mt-1.5"
              value={label.draft}
              disabled={locked}
              onChange={(event) => label.onChange(event.target.value)}
              onBlur={label.flush}
            />
          </label>
          {textMode && (
            <label className="block text-[13px]">
              朗读文字
              <input
                ref={text.ref}
                className="field mt-1.5"
                value={text.draft}
                disabled={locked}
                onChange={(event) => text.onChange(event.target.value)}
                onBlur={text.flush}
              />
            </label>
          )}
          <div className="flex flex-wrap gap-2">
            {!textMode && (
              <button
                className="btn icon-btn"
                title="导入音频"
                aria-label="导入音频"
                disabled={locked}
                onClick={() => run({ type: "import_slot_audio", index, slot })}
              >
                📥
              </button>
            )}
            <button
              className="btn icon-btn"
              title={playing ? "停止试听" : "试听"}
              aria-label={playing ? "停止试听" : "试听"}
              disabled={!hasAudio && !hasText}
              onClick={() => run({ type: "preview_slot", index, slot })}
            >
              {playing ? "⏹️" : "▶️"}
            </button>
            {!textMode && (
              <button
                className="btn icon-btn"
                title="截取片段"
                aria-label="截取片段"
                disabled={locked || !hasAudio}
                onClick={() => run({ type: "crop_slot_audio", index, slot })}
              >
                ✂️
              </button>
            )}
            <button
              className="btn icon-btn"
              title="清空槽位"
              aria-label="清空槽位"
              disabled={locked}
              onClick={() => run({ type: "clear_slot", index, slot })}
            >
              🧹
            </button>
          </div>
          {snapshot.locked && (
            <p className="text-xs text-[var(--muted)]">正在导入或保存音频，请稍候…</p>
          )}
        </div>
      </section>
    </>
  );
}

/// 输入框编辑期间不被快照回写覆盖；失焦或提交后以后端值为准。
function useSyncedInput(initial: string, commit: (value: string) => void) {
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
