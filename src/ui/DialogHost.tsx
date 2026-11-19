import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

type DialogRequest = {
  id: number;
  kind: "confirm" | "prompt" | "record";
  title: string;
  message: string;
  value: string;
  password: boolean;
  single: boolean;
};

export default function DialogHost() {
  const [dialog, setDialog] = useState<DialogRequest | null>(null);
  const [text, setText] = useState("");
  const [recording, setRecording] = useState("");
  const pending = useRef<{ code: string; binding: string } | null>(null);

  useEffect(() => {
    const stop = listen<DialogRequest>("wordy://dialog", (event) => {
      setDialog(event.payload);
      setText(event.payload.value);
      setRecording("");
      pending.current = null;
    });
    return () => {
      stop.then((off) => off());
    };
  }, []);

  useEffect(() => {
    if (dialog?.kind !== "record") return;
    const onKeyDown = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === "Escape") {
        reply(false, null);
        return;
      }
      if (pending.current) return;
      const binding = bindingFor(event, dialog.single);
      if (!binding) {
        setRecording(previewModifiers(event));
        return;
      }
      pending.current = { code: event.code, binding: binding.binding };
      setRecording(binding.display);
    };
    const onKeyUp = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      const active = pending.current;
      if (active && event.code === active.code) {
        pending.current = null;
        reply(true, active.binding);
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
    };
  });

  // 确认/输入弹窗的 Esc 在窗口层处理：焦点落在任一按钮上都只关掉这一层，不连带设置页。
  useEffect(() => {
    if (!dialog || dialog.kind === "record") return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      reply(false, null);
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [dialog]);

  if (!dialog) return null;

  function reply(ok: boolean, value: string | null) {
    const id = dialog?.id;
    setDialog(null);
    if (id === null || id === undefined) return;
    invoke("wordy_dialog_reply", { reply: { id, ok, value } }).catch((error) =>
      console.error(error),
    );
  }

  return (
    <div className="overlay">
      <section
        role="dialog"
        aria-modal="true"
        aria-label={dialog.title}
        className="overlay-card panel-in w-[26rem] p-5"
      >
        <h2 className="text-sm font-semibold">{dialog.title}</h2>
        {dialog.kind === "record" ? (
          <>
            <p className="mt-2 text-xs text-[var(--muted)]">{dialog.message}</p>
            {/* 不要加 block：它会覆盖 output.field 的 flex，文字就靠左了。 */}
            <output className="field mt-3 min-h-[2.4rem] justify-center text-lg">
              {recording || "…"}
            </output>
            <p className="mt-2 text-xs text-[var(--muted)]">松开即保存，按 Esc 取消</p>
            <div className="mt-4 flex justify-end">
              <button className="btn" onClick={() => reply(false, null)}>
                取消
              </button>
            </div>
          </>
        ) : dialog.kind === "prompt" ? (
          <>
            <p className="mt-2 text-xs text-[var(--muted)]">{dialog.message}</p>
            <input
              autoFocus
              className="field mt-2"
              type={dialog.password ? "password" : "text"}
              value={text}
              autoComplete="off"
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                // Enter 提交；Esc 由窗口级监听处理，避免两层一起关闭。
                if (event.key === "Enter") reply(true, text);
              }}
            />
            <div className="mt-4 flex justify-end gap-2">
              <button className="btn" onClick={() => reply(false, null)}>
                取消
              </button>
              <button className="btn primary" onClick={() => reply(true, text)}>
                确定
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="mt-2 whitespace-pre-wrap text-sm text-[var(--text)]">{dialog.message}</p>
            <div className="mt-4 flex justify-end gap-2">
              <button className="btn" autoFocus onClick={() => reply(false, null)}>
                取消
              </button>
              <button className="btn primary" onClick={() => reply(true, null)}>
                确定
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function previewModifiers(event: KeyboardEvent) {
  const parts: string[] = [];
  if (event.ctrlKey) parts.push("Ctrl");
  if (event.shiftKey) parts.push("Shift");
  if (event.altKey) parts.push("Alt");
  if (event.metaKey) parts.push("Win");
  return parts.length > 0 ? `${parts.join(" + ")} + …` : "";
}

function bindingFor(event: KeyboardEvent, single: boolean) {
  const key = keyName(event);
  if (!key) return null;
  if (single) return { binding: key, display: key.toUpperCase() };
  const parts: string[] = [];
  if (event.ctrlKey) parts.push("ctrl");
  if (event.shiftKey) parts.push("shift");
  if (event.altKey) parts.push("alt");
  if (event.metaKey) parts.push("windows");
  parts.push(key);
  const binding = parts.join("+");
  return {
    binding,
    display: parts
      .map((part) =>
        part === "ctrl"
          ? "Ctrl"
          : part === "shift"
            ? "Shift"
            : part === "alt"
              ? "Alt"
              : part === "windows"
                ? "Win"
                : part.length === 1
                  ? part.toUpperCase()
                  : part.replace(/\b\w/g, (letter) => letter.toUpperCase()),
      )
      .join(" + "),
  };
}

const KEY_BY_CODE: Record<string, string> = {
  Backspace: "backspace",
  Tab: "tab",
  Enter: "enter",
  Space: "space",
  Pause: "pause",
  CapsLock: "caps lock",
  PageUp: "page up",
  PageDown: "page down",
  End: "end",
  Home: "home",
  ArrowLeft: "left",
  ArrowUp: "up",
  ArrowRight: "right",
  ArrowDown: "down",
  PrintScreen: "print screen",
  Insert: "insert",
  Delete: "delete",
  ContextMenu: "menu",
  NumLock: "num lock",
  ScrollLock: "scroll lock",
  Semicolon: ";",
  Equal: "=",
  Comma: ",",
  Minus: "-",
  Period: ".",
  Slash: "/",
  Backquote: "`",
  BracketLeft: "[",
  Backslash: "\\",
  BracketRight: "]",
  Quote: "'",
  Numpad0: "num 0",
  Numpad1: "num 1",
  Numpad2: "num 2",
  Numpad3: "num 3",
  Numpad4: "num 4",
  Numpad5: "num 5",
  Numpad6: "num 6",
  Numpad7: "num 7",
  Numpad8: "num 8",
  Numpad9: "num 9",
};

function keyName(event: KeyboardEvent) {
  if (/^Key[A-Z]$/.test(event.code)) return event.code.slice(3).toLowerCase();
  if (/^Digit[0-9]$/.test(event.code)) return event.code.slice(5);
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(event.code)) return event.code.toLowerCase();
  return KEY_BY_CODE[event.code] ?? null;
}
