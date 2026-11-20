import { useState, type ReactNode } from "react";
import type { AudioClip, Wheel } from "./App";
import { Icon, Panel } from "./Controls";

type Props = {
  wheel: Wheel;
  wheelSelect: ReactNode;
  clips: AudioClip[];
  locked: boolean;
  assign: (slot: number, id: string) => void;
};

const CLIP_DRAG_TYPE = "application/x-wordy-audio";

export default function WheelAudioOrganizer({ wheel, wheelSelect, clips, locked, assign }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const [target, setTarget] = useState<number | null>(null);
  const names = new Map(clips.map(({ id, name }) => [id, name]));
  const selectedId = selected && names.has(selected) ? selected : null;
  const place = (slot: number, id: string) => {
    // 仅接受音频库中的 ID，忽略外部文件或文本拖放。
    if (locked || !names.has(id)) return;
    assign(slot, id);
    setSelected(null);
  };

  return (
    <>
      <Panel title="轮盘槽位" icon="wheels">
        {wheelSelect}
        <ol className="mt-3 grid grid-cols-2 gap-2">
          {wheel.slots.map((id, slot) => {
            const audio = id ? names.get(id) ?? "音频不可用" : "无音频";
            const label = wheel.labels[slot] || wheel.texts[slot];
            const readsText = Boolean(wheel.texts[slot] && (!id || wheel.text_modes[slot]));
            return (
              <li key={slot} className="min-w-0">
                <button
                  className="btn flex h-full min-h-15 w-full flex-col justify-center gap-0.5 py-2 text-left"
                  data-drop-target={target === slot || undefined}
                  disabled={locked}
                  aria-disabled={selectedId === null}
                  title={label && label !== audio ? `${label} · ${audio}` : audio}
                  onClick={() => selectedId && place(slot, selectedId)}
                  onDragOver={(event) => {
                    if (locked || !event.dataTransfer.types.includes(CLIP_DRAG_TYPE)) return;
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "copy";
                    setTarget(slot);
                  }}
                  onDragLeave={() => setTarget(null)}
                  onDrop={(event) => {
                    if (!event.dataTransfer.types.includes(CLIP_DRAG_TYPE)) return;
                    event.preventDefault();
                    setTarget(null);
                    place(slot, event.dataTransfer.getData(CLIP_DRAG_TYPE));
                  }}
                >
                  <span className="pointer-events-none w-full truncate text-xs text-[var(--muted)]">
                    第 {slot + 1} 槽{readsText && " · 文字朗读"}
                  </span>
                  <span className="pointer-events-none w-full truncate">{audio}</span>
                  {readsText && label && (
                    <span className="pointer-events-none w-full truncate text-xs text-[var(--muted)]">
                      {label}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ol>
      </Panel>

      <Panel title="音频库" icon="audio">
        {clips.length ? (
          <>
            <p className="mb-3 text-sm text-[var(--muted)]">
              拖动音频到轮盘槽位，或先选中音频，再点击槽位。
            </p>
            <ul className="max-h-[440px] space-y-2 overflow-y-auto p-1">
              {clips.map(({ id, name }) => (
                <li key={id}>
                  <button
                    className="btn flex w-full cursor-grab items-center gap-2 text-left"
                    draggable={!locked}
                    disabled={locked}
                    aria-pressed={selectedId === id}
                    title={name}
                    onClick={() => setSelected(selectedId === id ? null : id)}
                    onDragStart={(event) => {
                      event.dataTransfer.effectAllowed = "copy";
                      event.dataTransfer.setData(CLIP_DRAG_TYPE, id);
                    }}
                    onDragEnd={() => setTarget(null)}
                  >
                    <span className="shrink-0 text-[var(--muted)]"><Icon name="audio" /></span>
                    <span className="truncate">{name}</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-[var(--muted)]">暂无音频。可在编辑轮盘中导入，或通过录制添加。</p>
        )}
      </Panel>
    </>
  );
}
