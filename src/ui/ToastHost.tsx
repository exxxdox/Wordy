import { useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";

type Feedback = { message: string; tone: "success" | "error" };

type Card = Feedback & { id: number };

export default function ToastHost() {
  const [cards, setCards] = useState<Card[]>([]);
  const next = useRef(0);

  useEffect(() => {
    const stop = listen<Feedback>("wordy://feedback", (event) => {
      const card = { ...event.payload, id: ++next.current };
      setCards((current) => [...current.slice(-2), card]);
      window.setTimeout(() => {
        setCards((current) => current.filter((item) => item.id !== card.id));
      }, 2800);
    });
    return () => {
      stop.then((off) => off());
    };
  }, []);

  if (cards.length === 0) return null;

  return (
    <div className="pointer-events-none absolute bottom-16 right-6 z-40 flex w-80 flex-col gap-2">
      {cards.map((card) => (
        <div
          key={card.id}
          className={`rounded-xl border px-3 py-2 text-sm shadow-lg backdrop-blur ${
            card.tone === "error"
              ? "border-red-400/40 bg-red-950/85 text-red-100"
              : "border-white/15 bg-[#1d1d21]/95 text-[var(--text)]"
          }`}
        >
          {card.message}
        </div>
      ))}
    </div>
  );
}
