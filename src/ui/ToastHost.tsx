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
    <div
      aria-live="polite"
      className="pointer-events-none absolute bottom-16 right-6 z-40 flex w-80 flex-col gap-2"
    >
      {cards.map((card) => (
        <div
          key={card.id}
          className={`toast rounded-xl border px-3 py-2 text-sm shadow-lg backdrop-blur ${
            card.tone === "error"
              ? "border-[var(--danger)]/45 bg-[#2a1b19]/95 text-[#f3cfc9]"
              : "border-[var(--line)] bg-[var(--panel-hi)]/95 text-[var(--text)]"
          }`}
        >
          {card.message}
        </div>
      ))}
    </div>
  );
}
