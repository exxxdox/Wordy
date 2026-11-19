import { useEffect, useRef } from "react";

export type WheelCanvasStyle = "classic" | "glass" | "petals" | "game";

type Props = {
  labels: string[];
  style: string;
  selected: number;
  onSelectSlot?: (slot: number) => void;
  className?: string;
};

const CANVAS_WIDTH = 634;
const CANVAS_HEIGHT = 540;
const CENTER_X = CANVAS_WIDTH / 2;
const CENTER_Y = CANVAS_HEIGHT / 2;
const DISC_RADIUS = 246;
const RING_INNER = 80;

const PALETTES: Record<
  WheelCanvasStyle,
  { disc: [string, string]; wedge: [string, string]; accent: string; label: string }
> = {
  classic: {
    disc: ["#3b3d42", "#1b1c20"],
    wedge: ["#3a3b40", "#242529"],
    accent: "#0a84ff",
    label: "#f5f5f7",
  },
  glass: {
    disc: ["rgba(255,255,255,0.28)", "rgba(120,130,150,0.16)"],
    wedge: ["rgba(255,255,255,0.30)", "rgba(160,180,200,0.10)"],
    accent: "#7cc4ff",
    label: "#ffffff",
  },
  petals: {
    disc: ["#2c2d33", "#17181c"],
    wedge: ["#43454c", "#26272c"],
    accent: "#ff9f45",
    label: "#fff5ea",
  },
  game: {
    disc: ["#2a2b31", "#101115"],
    wedge: ["#34363d", "#1d1e23"],
    accent: "#ff5a3c",
    label: "#ffffff",
  },
};

function styleOf(style: string): WheelCanvasStyle {
  return style === "glass" || style === "petals" || style === "game" ? style : "classic";
}

export default function WheelCanvas({ labels, style, selected, onSelectSlot, className }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const wheelStyle = styleOf(style);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const draw = () => renderWheel(element, wheelStyle, labels, selected);
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(element);
    return () => observer.disconnect();
  }, [wheelStyle, labels, selected]);

  return (
    <canvas
      ref={canvas}
      className={className ?? "h-auto w-full"}
      onClick={(event) => {
        if (!onSelectSlot) return;
        const rect = event.currentTarget.getBoundingClientRect();
        const scale = CANVAS_WIDTH / rect.width;
        const x = (event.clientX - rect.left) * scale;
        const y = (event.clientY - rect.top) * scale;
        const dx = x - CENTER_X;
        const dy = y - CENTER_Y;
        const radius = Math.hypot(dx, dy);
        if (radius < RING_INNER || radius > DISC_RADIUS) return;
        const angle = ((Math.atan2(dx, -dy) * 180) / Math.PI + 360) % 360;
        onSelectSlot(Math.floor(((angle + 22.5) % 360) / 45) % 8);
      }}
    />
  );
}

function renderWheel(
  canvas: HTMLCanvasElement,
  style: WheelCanvasStyle,
  labels: string[],
  selected: number,
) {
  const width = canvas.clientWidth;
  if (width <= 0) return;
  const dpr = window.devicePixelRatio || 1;
  const height = (width * CANVAS_HEIGHT) / CANVAS_WIDTH;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const scale = (width / CANVAS_WIDTH) * dpr;
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.clearRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);
  const palette = PALETTES[style];

  const disc = ctx.createLinearGradient(
    CENTER_X - 220,
    CENTER_Y - 240,
    CENTER_X + 220,
    CENTER_Y + 250,
  );
  disc.addColorStop(0, palette.disc[0]);
  disc.addColorStop(1, palette.disc[1]);
  ctx.save();
  ctx.shadowColor = "rgba(0,0,0,0.55)";
  ctx.shadowBlur = 28;
  ctx.beginPath();
  ctx.arc(CENTER_X, CENTER_Y, DISC_RADIUS, 0, Math.PI * 2);
  ctx.fillStyle = disc;
  ctx.fill();
  ctx.restore();
  ctx.beginPath();
  ctx.arc(CENTER_X, CENTER_Y, DISC_RADIUS, 0, Math.PI * 2);
  ctx.strokeStyle = "rgba(255,255,255,0.22)";
  ctx.lineWidth = 1;
  ctx.stroke();

  for (let index = 0; index < 8; index++) {
    const centerAngle = ((index * 45 - 90) * Math.PI) / 180;
    const start = centerAngle - (22.5 * Math.PI) / 180;
    const end = centerAngle + (22.5 * Math.PI) / 180;
    const active = index === selected;
    const wedge = ctx.createRadialGradient(
      CENTER_X,
      CENTER_Y,
      RING_INNER,
      CENTER_X,
      CENTER_Y,
      DISC_RADIUS,
    );
    wedge.addColorStop(0, active ? palette.accent : palette.wedge[1]);
    wedge.addColorStop(1, active ? palette.accent : palette.wedge[0]);
    ctx.beginPath();
    if (style === "petals") {
      const outerLeft = polar(DISC_RADIUS, start);
      const tip = polar(52, centerAngle);
      const control = polar(150, centerAngle);
      ctx.moveTo(tip.x, tip.y);
      ctx.quadraticCurveTo(control.x, control.y, outerLeft.x, outerLeft.y);
      ctx.arc(CENTER_X, CENTER_Y, DISC_RADIUS, start, end);
      ctx.quadraticCurveTo(control.x, control.y, tip.x, tip.y);
      ctx.closePath();
    } else {
      ctx.arc(CENTER_X, CENTER_Y, DISC_RADIUS, start, end);
      ctx.arc(CENTER_X, CENTER_Y, RING_INNER, end, start, true);
      ctx.closePath();
    }
    ctx.fillStyle = wedge;
    ctx.globalAlpha = active ? 0.95 : 0.82;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = active
      ? palette.accent
      : style === "glass"
        ? "rgba(255,255,255,0.28)"
        : "rgba(255,255,255,0.10)";
    ctx.lineWidth = active ? 1.6 : 0.8;
    ctx.stroke();

    const number = polar(188, centerAngle);
    ctx.fillStyle = active ? "#ffffff" : "rgba(255,255,255,0.45)";
    ctx.font = '13px "Segoe UI Variable Text", "Segoe UI", sans-serif';
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(String(index + 1).padStart(2, "0"), number.x, number.y);

    const label = labels[index] ?? "";
    if (label) {
      const spot = polar(150, centerAngle);
      ctx.fillStyle = active ? "#ffffff" : palette.label;
      ctx.font =
        'bold 14px "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif';
      ctx.fillText(label, spot.x, spot.y, 94);
    }
  }

  if (style === "game") {
    ctx.beginPath();
    ctx.arc(CENTER_X, CENTER_Y, DISC_RADIUS - 8, 0, Math.PI * 2);
    ctx.strokeStyle = palette.accent;
    ctx.lineWidth = 4;
    ctx.stroke();
  }

  const hub = ctx.createRadialGradient(
    CENTER_X - 20,
    CENTER_Y - 30,
    8,
    CENTER_X,
    CENTER_Y,
    80,
  );
  hub.addColorStop(0, "rgba(52,54,60,0.98)");
  hub.addColorStop(1, "rgba(20,21,25,0.98)");
  ctx.beginPath();
  ctx.arc(CENTER_X, CENTER_Y, 76, 0, Math.PI * 2);
  ctx.fillStyle = hub;
  ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.24)";
  ctx.lineWidth = 1.2;
  ctx.stroke();
  ctx.fillStyle = "rgba(255,255,255,0.72)";
  ctx.font = '13px "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif';
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("选择槽位", CENTER_X, CENTER_Y);
}

function polar(radius: number, angle: number) {
  return {
    x: CENTER_X + Math.cos(angle) * radius,
    y: CENTER_Y + Math.sin(angle) * radius,
  };
}
