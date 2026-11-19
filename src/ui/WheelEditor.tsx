import { useEffect, useState } from "react";
import type { Snapshot, Wheel } from "./App";
import { Icon, Panel, Slider, displayHotkey, useSyncedInput } from "./Controls";
import WheelCanvas from "./WheelCanvas";

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
      <Panel title="轮盘与快捷键">
        <div className="flex flex-col gap-3">
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
              <Icon name="plus" />
            </button>
            <button
              className="btn icon-btn"
              title="删除轮盘"
              aria-label="删除轮盘"
              disabled={locked || wheels.length <= 1}
              onClick={() => run({ type: "remove_wheel", index })}
            >
              <Icon name="trash" />
            </button>
          </div>
          <div className="flex gap-2">
            <output className="field min-w-0 flex-1 truncate" title="当前快捷键">
              {displayHotkey(wheel.hotkey) || "未绑定快捷键"}
            </output>
            <button
              className="btn"
              title="录制快捷键"
              disabled={locked}
              onClick={() => run({ type: "record_wheel_hotkey", index })}
            >
              录制
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
          <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--field)] p-2">
            <div className="relative mx-auto aspect-[634/540] w-full max-w-[420px]">
              <WheelCanvas
                labels={wheelLabels(wheel)}
                font={settings.ui_font}
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
      </Panel>

      <Panel title={`槽位内容 · 第 ${slot + 1} 槽`}>
        <div className="flex flex-col gap-3">
          <div>
            <div className="mb-1.5 text-[13px]">内容类型</div>
            <div className="seg" role="group" aria-label="内容类型">
              <button
                className="seg-item"
                aria-pressed={!textMode}
                disabled={locked}
                onClick={() => {
                  setEmptyTextMode(false);
                  if (hasText) run({ type: "set_slot_mode", index, slot, text: false });
                }}
              >
                音频
              </button>
              <button
                className="seg-item"
                aria-pressed={textMode}
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
            <Slider
              label="音量"
              value={gain}
              min={0}
              max={300}
              step={5}
              display={`${gain}%`}
              disabled={locked || !hasAudio}
              onChange={(value) => {
                setGain(value);
                run({ type: "set_slot_volume", index, slot, value });
              }}
            />
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
                导入音频
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
              className="btn ghost"
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
      </Panel>
    </>
  );
}
