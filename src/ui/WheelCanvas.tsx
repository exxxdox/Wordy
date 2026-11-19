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
// 与 wheel_paint 的 wedge(256.0, 62.0) 一致：点击范围必须等于实际绘制的扇区，
// 否则扇区内沿（62–80）和淡出外沿（232–256）会出现点不中或误选。
const RING_INNER = 62;
const RING_OUTER = 256;

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
          const header = new DataView(buffer);
          const pixelWidth = header.getUint32(0, true);
          const pixelHeight = header.getUint32(4, true);
          if (element.width !== pixelWidth || element.height !== pixelHeight) {
            element.width = pixelWidth;
            element.height = pixelHeight;
          }
          // 原生已输出直通 RGBA，WebView 可直接交给 Canvas，无需逐像素转换。
          const pixels = new Uint8ClampedArray(buffer, 8, pixelWidth * pixelHeight * 4);
          element
            .getContext("2d")
            ?.putImageData(new ImageData(pixels, pixelWidth, pixelHeight), 0, 0);
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
