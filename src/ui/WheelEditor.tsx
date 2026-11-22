import { useEffect, useState } from "react";
import type { Snapshot, Wheel } from "./App";
import { Icon, Panel, Slider, displayHotkey, useSyncedInput } from "./Controls";
import WheelAudioOrganizer from "./WheelAudioOrganizer";
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
      text_modes: settings.chat_wheel_text_modes,
      volumes: settings.chat_wheel_volumes,
    },
    ...settings.additional_chat_wheels,
  ];
}

function slotTitle(wheel: Wheel, slot: number) {
  if (wheel.labels[slot]) return wheel.labels[slot];
  if (wheel.text_modes[slot] && wheel.texts[slot]) return wheel.texts[slot];
  return wheel.slots[slot] ? "音频" : wheel.texts[slot];
}

function wheelLabels(wheel: Wheel) {
  return Array.from({ length: 8 }, (_, index) => {
    if (!wheel.slots[index] && !wheel.texts[index]) return "";
    if (wheel.labels[index]) return wheel.labels[index];
    return wheel.text_modes[index]
      ? wheel.texts[index] || wheel.slots[index]
      : wheel.slots[index] || wheel.texts[index];
  });
}

export default function WheelEditor({ snapshot, run }: Props) {
  const settings = snapshot.settings;
  const wheels = wheelsOf(snapshot);
  const index = Math.min(settings.chat_wheel_last_index, wheels.length - 1);
  const wheel = wheels[index] ?? wheels[0];
  const [organizing, setOrganizing] = useState(false);
  const [slot, setSlot] = useState(0);
  const [gain, setGain] = useState(Math.round((wheel?.volumes[slot] ?? 1) * 100));

  const hasAudio = Boolean(wheel?.slots[slot]);
  const hasText = Boolean(wheel?.texts[slot]);
  // 模式持久化在配置里：两类内容可以同时保留，来回切换不会丢数据。
  const textMode = Boolean(wheel?.text_modes[slot]);
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
    setGain(Math.round((wheel?.volumes[slot] ?? 1) * 100));
  }, [wheel?.volumes[slot]]);

  if (!wheel) return null;

  const modeSwitch = (
    <div className="seg col-span-full w-fit" role="group" aria-label="轮盘设置视图">
      <button className="seg-item px-4" aria-pressed={!organizing} onClick={() => setOrganizing(false)}>
        编辑轮盘
      </button>
      <button
        className="seg-item px-4"
        aria-pressed={organizing}
        onClick={() => {
          if (!organizing) run({ type: "refresh_audio_library" });
          setOrganizing(true);
        }}
      >
        整理音频
      </button>
    </div>
  );
  const wheelSelect = (
    <select
      className="field min-w-0 flex-1"
      aria-label={organizing ? "整理目标轮盘" : "当前轮盘"}
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
  );

  if (organizing) {
    return (
      <>
        {modeSwitch}
        <WheelAudioOrganizer
          wheel={wheel}
          wheelSelect={wheelSelect}
          clips={snapshot.audio_clips}
          font={settings.ui_font}
          locked={locked || snapshot.busy}
          assign={(slot, id) => run({ type: "assign_wheel_audio", index, slot, id })}
          deleteClip={(id) => run({ type: "delete_library_audio", id })}
          cleanUnused={() => run({ type: "clean_unused_audio" })}
        />
      </>
    );
  }

  const clipName = snapshot.audio_clips.find((clip) => clip.id === wheel.slots[slot])?.name;
  return (
    <>
      {modeSwitch}
      <Panel title="轮盘与快捷键" icon="wheels">
        <div className="flex flex-col gap-3">
          <div className="flex gap-2">
            {wheelSelect}
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
              className="btn icon-btn danger"
              title="删除轮盘"
              aria-label="删除轮盘"
              disabled={locked || wheels.length <= 1}
              onClick={() => run({ type: "remove_wheel", index })}
            >
              <Icon name="trash" />
            </button>
          </div>
          <div className="hotkey-control">
            <kbd title="当前快捷键">
              {displayHotkey(wheel.hotkey) || "未绑定快捷键"}
            </kbd>
            <button
              className="btn record-btn"
              aria-label="录制轮盘快捷键"
              disabled={locked}
              onClick={() => run({ type: "record_wheel_hotkey", index })}
            >
              录制
            </button>
          </div>
          <label className="block text-sm">
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
          <div className="wheel-preview">
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
        </div>
      </Panel>

      <Panel title={`槽位内容 · 第 ${slot + 1} 槽`} icon="audio">
        <div className="flex flex-col gap-3">
          <label className="block text-sm">
            槽位
            {/* 下拉框与扇区共用选中状态，键盘也能选择槽位。 */}
            <select
              className="field mt-1.5"
              value={slot}
              onChange={(event) => setSlot(Number(event.target.value))}
            >
              {wheel.slots.map((_, i) => {
                const title = slotTitle(wheel, i);
                return (
                  <option key={i} value={i}>
                    第 {i + 1} 槽{title && ` · ${title}`}
                  </option>
                );
              })}
            </select>
          </label>
          <div>
            <div className="mb-1.5 text-sm">内容类型</div>
            <div className="seg" role="group" aria-label="内容类型">
              <button
                className="seg-item"
                aria-pressed={!textMode}
                disabled={locked}
                onClick={() => {
                  if (textMode) run({ type: "set_slot_mode", index, slot, text: false });
                }}
              >
                音频
              </button>
              <button
                className="seg-item"
                aria-pressed={textMode}
                disabled={locked}
                onClick={() => {
                  if (!textMode) run({ type: "set_slot_mode", index, slot, text: true });
                }}
              >
                文字
              </button>
            </div>
            <p className="mt-1 wrap-anywhere text-xs text-[var(--muted)]">
              {textMode
                ? hasText
                  ? "当前槽以文字朗读"
                  : "输入文字后可试听朗读"
                : hasAudio
                  ? // 名称可能重复，附上文件 ID 才能唯一区分当前的音频文件。
                    clipName
                    ? `${clipName} · ${wheel.slots[slot]}`
                    : wheel.slots[slot]
                  : "导入音频后可试听"}
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
          <label className="block text-sm">
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
            <label className="block text-sm">
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
                className="btn primary"
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
