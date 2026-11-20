import { useEffectEvent, useLayoutEffect, useRef, type MouseEvent } from "react";
import { invoke } from "@tauri-apps/api/core";
import { watchWheelPreview } from "./wheel-preview";

type Props = {
  labels: string[];
  font: string;
  selected: number | null;
  onSelectSlot?: (slot: number) => void;
  drop?: {
    type: string;
    onHover: (slot: number | null) => void;
    onDrop: (slot: number, value: string) => void;
  };
  className?: string;
};

const CANVAS_WIDTH = 634;
const CANVAS_HEIGHT = 540;
const CENTER_X = CANVAS_WIDTH / 2;
const CENTER_Y = CANVAS_HEIGHT / 2;
// 与 wheel_paint 的 wedge(256.0, 62.0) 一致：点击范围必须等于实际绘制的扇区，
// 否则扇区内沿（62–80）和淡出外沿（232–256）会出现点不中或误选。
const RING_INNER = 62;
const RING_OUTER = 256;

function slotAt({ currentTarget, clientX, clientY }: MouseEvent<HTMLCanvasElement>) {
  const rect = currentTarget.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const dx = ((clientX - rect.left) * CANVAS_WIDTH) / rect.width - CENTER_X;
  const dy = ((clientY - rect.top) * CANVAS_HEIGHT) / rect.height - CENTER_Y;
  const radius = Math.hypot(dx, dy);
  if (radius < RING_INNER || radius > RING_OUTER) return null;
  const angle = ((Math.atan2(dx, -dy) * 180) / Math.PI + 360) % 360;
  return Math.floor(((angle + 22.5) % 360) / 45);
}

export default function WheelCanvas({ labels, font, selected, onSelectSlot, drop, className }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const preview = useRef<ReturnType<typeof watchWheelPreview> | null>(null);
  // 上层经常重建标签数组，只有标签值变化才需要新预览。
  const labelsKey = JSON.stringify(labels);
  const readPreview = useEffectEvent(() => {
    const width = canvas.current?.clientWidth ?? 0;
    if (width <= 0) return null;
    const scale = (width * (window.devicePixelRatio || 1)) / CANVAS_WIDTH;
    return { labels, selected, font, scale };
  });

  useLayoutEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const watcher = watchWheelPreview(
      element,
      () => readPreview(),
      (input) => invoke<ArrayBuffer>("wordy_wheel_preview", input),
      (buffer) => {
        const header = new DataView(buffer);
        const pixelWidth = header.getUint32(0, true);
        const pixelHeight = header.getUint32(4, true);
        if (element.width !== pixelWidth || element.height !== pixelHeight) {
          element.width = pixelWidth;
          element.height = pixelHeight;
        }
        // 原生已输出直通 RGBA，WebView 可直接交给 Canvas，无需逐像素转换。
        const pixels = new Uint8ClampedArray(buffer, 8, pixelWidth * pixelHeight * 4);
        element.getContext("2d")?.putImageData(new ImageData(pixels, pixelWidth, pixelHeight), 0, 0);
      },
    );
    preview.current = watcher;
    return () => {
      preview.current = null;
      watcher.dispose();
    };
  }, []);

  useLayoutEffect(() => {
    preview.current?.update();
  }, [labelsKey, selected, font]);

  return (
    <canvas
      ref={canvas}
      className={className ?? "h-auto w-full"}
      onClick={(event) => {
        if (!onSelectSlot) return;
        const slot = slotAt(event);
        if (slot !== null) onSelectSlot(slot);
      }}
      onDragOver={(event) => {
        if (!drop || !event.dataTransfer.types.includes(drop.type)) return;
        const slot = slotAt(event);
        drop.onHover(slot);
        if (slot !== null) {
          event.preventDefault();
          event.dataTransfer.dropEffect = "copy";
        }
      }}
      onDragLeave={() => drop?.onHover(null)}
      onDrop={(event) => {
        if (!drop || !event.dataTransfer.types.includes(drop.type)) return;
        event.preventDefault();
        drop.onHover(null);
        const slot = slotAt(event);
        if (slot !== null) drop.onDrop(slot, event.dataTransfer.getData(drop.type));
      }}
    />
  );
}
