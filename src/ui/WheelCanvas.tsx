import { useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";

type Props = {
  labels: string[];
  font: string;
  selected: number;
  onSelectSlot?: (slot: number) => void;
  className?: string;
};

const CANVAS_WIDTH = 634;
const CANVAS_HEIGHT = 540;
const CENTER_X = CANVAS_WIDTH / 2;
const CENTER_Y = CANVAS_HEIGHT / 2;
const RING_INNER = 80;
const RING_OUTER = 232;

export default function WheelCanvas({ labels, font, selected, onSelectSlot, className }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const latest = useRef(0);
  // 数组每次渲染都是新引用，用拼接值判断标签是否真的变化。
  const labelsKey = labels.join("\u0000");

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    let disposed = false;
    let scheduled = 0;

    const draw = () => {
      const width = element.clientWidth;
      if (width <= 0) return;
      const scale = (width * (window.devicePixelRatio || 1)) / CANVAS_WIDTH;
      const id = ++latest.current;
      invoke<ArrayBuffer>("wordy_wheel_preview", { labels, selected, font, scale })
        .then((buffer) => {
          if (disposed || id !== latest.current) return;
          const view = new DataView(buffer);
          const pixelWidth = view.getUint32(0, true);
          const pixelHeight = view.getUint32(4, true);
          const source = new Uint8Array(buffer, 8);
          const image = new ImageData(pixelWidth, pixelHeight);
          for (let index = 0; index < source.length; index += 4) {
            const alpha = source[index + 3];
            // 原生位图是预乘 BGRA；Canvas 需要直通 RGBA。
            const lift = alpha === 0 || alpha === 255 ? 1 : 255 / alpha;
            image.data[index] = source[index + 2] * lift;
            image.data[index + 1] = source[index + 1] * lift;
            image.data[index + 2] = source[index] * lift;
            image.data[index + 3] = alpha;
          }
          if (element.width !== pixelWidth || element.height !== pixelHeight) {
            element.width = pixelWidth;
            element.height = pixelHeight;
          }
          element.getContext("2d")?.putImageData(image, 0, 0);
        })
        .catch((error) => console.error(error));
    };

    // 窗口缩放时 ResizeObserver 连续触发，合并到下一帧只渲染一次。
    const schedule = () => {
      window.cancelAnimationFrame(scheduled);
      scheduled = window.requestAnimationFrame(draw);
    };

    draw();
    const observer = new ResizeObserver(schedule);
    observer.observe(element);
    return () => {
      disposed = true;
      window.cancelAnimationFrame(scheduled);
      observer.disconnect();
    };
  }, [labelsKey, selected, font]);

  return (
    <canvas
      ref={canvas}
      className={className ?? "h-auto w-full"}
      onClick={(event) => {
        if (!onSelectSlot) return;
        const rect = event.currentTarget.getBoundingClientRect();
        const x = ((event.clientX - rect.left) * CANVAS_WIDTH) / rect.width;
        const y = ((event.clientY - rect.top) * CANVAS_HEIGHT) / rect.height;
        const dx = x - CENTER_X;
        const dy = y - CENTER_Y;
        const radius = Math.hypot(dx, dy);
        if (radius < RING_INNER || radius > RING_OUTER) return;
        const angle = ((Math.atan2(dx, -dy) * 180) / Math.PI + 360) % 360;
        onSelectSlot(Math.floor(((angle + 22.5) % 360) / 45) % 8);
      }}
    />
  );
}
