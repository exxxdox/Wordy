import { useState, type ReactNode } from "react";
import type { AudioClip, Wheel } from "./App";
import { Icon, Panel } from "./Controls";
import WheelCanvas from "./WheelCanvas";

type Props = {
  wheel: Wheel;
  wheelSelect: ReactNode;
  clips: AudioClip[];
  font: string;
  locked: boolean;
  assign: (slot: number, id: string) => void;
  deleteClip: (id: string) => void;
  cleanUnused: () => void;
};

const CLIP_DRAG_TYPE = "application/x-wordy-audio";

export default function WheelAudioOrganizer({
  wheel, wheelSelect, clips, font, locked, assign, deleteClip, cleanUnused,
}: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const [dragSlot, setDragSlot] = useState<number | null>(null);
  const [keyboardSlot, setKeyboardSlot] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [unusedOnly, setUnusedOnly] = useState(false);
  const names = new Map(clips.map(({ id, name }) => [id, name]));
  const query = search.trim().toLocaleLowerCase();
  const unused = clips.filter(({ referenced }) => !referenced);
  // 清理针对整个音频库，搜索只影响当前显示。
  const candidates = unusedOnly ? unused : clips;
  const visibleClips = query
    ? candidates.filter(({ name }) => name.toLocaleLowerCase().includes(query))
    : candidates;
  const selectedId = visibleClips.some(({ id }) => id === selected) ? selected : null;
  const labels = wheel.slots.map((id, slot) =>
    id ? names.get(id) ?? "音频不可用" : wheel.texts[slot] ? wheel.labels[slot] || wheel.texts[slot] : "",
  );
  const target = dragSlot ?? keyboardSlot;
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
        <div
          role="group"
          aria-label="音频轮盘"
          aria-describedby="wheel-audio-instructions"
          tabIndex={locked ? -1 : 0}
          className="wheel-preview mt-3 focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
          onFocus={(event) => {
            if (event.currentTarget.matches(":focus-visible")) setKeyboardSlot(0);
          }}
          onBlur={() => setKeyboardSlot(null)}
          onKeyDown={(event) => {
            if (locked) return;
            const current = keyboardSlot ?? 0;
            if (event.key === "ArrowRight" || event.key === "ArrowDown") {
              setKeyboardSlot((current + 1) % labels.length);
            } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
              setKeyboardSlot((current + labels.length - 1) % labels.length);
            } else if (selectedId && (event.key === "Enter" || event.key === " ")) {
              place(current, selectedId);
            } else {
              return;
            }
            event.preventDefault();
          }}
        >
          <div className="relative mx-auto aspect-[634/540] w-full max-w-[420px]">
            <WheelCanvas
              labels={labels}
              font={font}
              selected={locked ? null : target}
              className="absolute inset-0 h-full w-full"
              onSelectSlot={selectedId ? (slot) => place(slot, selectedId) : undefined}
              drop={locked ? undefined : {
                type: CLIP_DRAG_TYPE,
                onHover: setDragSlot,
                onDrop: place,
              }}
            />
          </div>
        </div>
        <p id="wheel-audio-instructions" className="sr-only">
          使用方向键选择槽位，按回车放入选中的音频。
        </p>
        <p className="sr-only" role="status">
          {keyboardSlot !== null && `第 ${keyboardSlot + 1} 槽，${labels[keyboardSlot] || "空槽"}`}
        </p>
      </Panel>

      <Panel title="音频库" icon="audio">
        <input
          type="search"
          aria-label="搜索音频"
          placeholder="搜索音频"
          className="field mb-3 w-full"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setSelected(null);
          }}
        />
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              role="switch"
              className="check"
              checked={unusedOnly}
              onChange={(event) => {
                setUnusedOnly(event.target.checked);
                setSelected(null);
              }}
            />
            仅显示未引用
          </label>
          <button
            className="btn"
            disabled={locked || unused.length === 0}
            onClick={cleanUnused}
          >
            清理未引用音频（{unused.length}）
          </button>
        </div>
        {visibleClips.length ? (
          <>
            <p className="mb-3 text-sm text-[var(--muted)]">
              拖到轮盘扇区，或先选择音频，再点击扇区。
            </p>
            <ul className="max-h-[440px] space-y-2 overflow-y-auto p-1">
              {visibleClips.map(({ id, name }) => (
                <li key={id} className="flex gap-2">
                  <button
                    className="btn flex min-w-0 flex-1 cursor-grab items-center gap-2 text-left"
                    draggable={!locked}
                    disabled={locked}
                    aria-pressed={selectedId === id}
                    title={name}
                    onClick={() => setSelected(selectedId === id ? null : id)}
                    onDragStart={(event) => {
                      event.dataTransfer.effectAllowed = "copy";
                      event.dataTransfer.setData(CLIP_DRAG_TYPE, id);
                    }}
                    onDragEnd={() => setDragSlot(null)}
                  >
                    <span className="shrink-0 text-[var(--muted)]"><Icon name="audio" /></span>
                    <span className="truncate">{name}</span>
                  </button>
                  <button
                    className="btn icon-btn shrink-0"
                    aria-label={`删除音频：${name}`}
                    title="删除音频"
                    disabled={locked}
                    onClick={() => deleteClip(id)}
                  >
                    <Icon name="trash" />
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-[var(--muted)]">
            {clips.length === 0
              ? "暂无音频。可在编辑轮盘中导入，或通过录制添加。"
              : candidates.length === 0
                ? "没有未引用的音频"
                : "未找到匹配的音频"}
          </p>
        )}
      </Panel>
    </>
  );
}
