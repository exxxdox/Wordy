import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { Snapshot, Wheel } from "./App";
import { displayHotkey } from "./App";

const STYLE_OPTIONS = [
  { value: "classic", label: "经典玻璃" },
  { value: "glass", label: "轻薄玻璃" },
  { value: "petals", label: "悬浮花瓣" },
  { value: "game", label: "竞技轮盘" },
];

const SLOT_COUNT = 8;

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

export default function WheelEditor({ snapshot, run }: Props) {
  const settings = snapshot.settings;
  const wheels = wheelsOf(snapshot);
  const index = Math.min(settings.chat_wheel_last_index, wheels.length - 1);
  const wheel = wheels[index] ?? wheels[0];
  const [slot, setSlot] = useState(0);
  const [emptyTextMode, setEmptyTextMode] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
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

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      invoke<string>("wordy_wheel_preview", { wheel: index, slot })
        .then((source) => {
          if (!cancelled) setPreview(source);
        })
        .catch(() => undefined);
    }, 120);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [index, slot, settings, wheel]);

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
            <button className="btn" disabled={locked} onClick={() => run({ type: "add_wheel" })}>
              新增
            </button>
            <button
              className="btn"
              disabled={locked || wheels.length <= 1}
              onClick={() => run({ type: "remove_wheel", index })}
            >
              删除
            </button>
          </div>
          <div className="flex gap-2">
            <output className="field min-w-0 flex-1 truncate">
              {displayHotkey(wheel.hotkey) || "未绑定快捷键"}
            </output>
            <button
              className="btn"
              disabled={locked}
              onClick={() => run({ type: "record_wheel_hotkey", index })}
            >
              录制快捷键
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
            <div className="grid grid-cols-4 gap-1.5">
              {Array.from({ length: SLOT_COUNT }, (_, order) => (
                <button
                  key={order}
                  onClick={() => setSlot(order)}
                  className={`truncate rounded-lg border px-2 py-1.5 text-xs ${
                    order === slot
                      ? "border-[var(--accent)] bg-[var(--accent)]/20 text-white"
                      : "border-white/10 bg-white/5 text-[var(--muted)] hover:text-white"
                  }`}
                >
                  {slotTitle(wheel, order)}
                </button>
              ))}
            </div>
          </div>
          <div className="rounded-xl border border-white/10 bg-black/20 p-2">
            {preview ? (
              <img src={preview} alt="轮盘预览" className="mx-auto h-auto w-full max-w-[420px]" />
            ) : (
              <div className="flex h-40 items-center justify-center text-xs text-[var(--muted)]">
                正在生成预览…
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
        <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-wider text-[var(--muted)]">
          槽位内容
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
                className="btn"
                disabled={locked}
                onClick={() => run({ type: "import_slot_audio", index, slot })}
              >
                导入
              </button>
            )}
            <button
              className="btn"
              disabled={!hasAudio && !hasText}
              onClick={() => run({ type: "preview_slot", index, slot })}
            >
              {playing ? "停止" : "试听"}
            </button>
            {!textMode && (
              <button
                className="btn"
                disabled={locked || !hasAudio}
                onClick={() => run({ type: "crop_slot_audio", index, slot })}
              >
                截取
              </button>
            )}
            <button
              className="btn"
              disabled={locked}
              onClick={() => run({ type: "clear_slot", index, slot })}
            >
              清空
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
