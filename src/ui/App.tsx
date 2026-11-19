import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import WheelEditor from "./WheelEditor";
import TrimDialog from "./TrimDialog";
import DialogHost from "./DialogHost";
import ToastHost from "./ToastHost";

type Provider = "Cartesia" | "Volcengine";

type Voice = { id: string; name: string };

type AudioIdentity = {
  name: string;
  host_api_name?: string | null;
  id?: string | null;
};

export type Wheel = {
  name: string;
  hotkey: string;
  slots: string[];
  labels: string[];
  texts: string[];
  volumes: number[];
};

export type Settings = {
  hotkey: string;
  chat_wheel_hotkey: string;
  chat_wheel_name: string;
  chat_wheel_slots: string[];
  chat_wheel_labels: string[];
  chat_wheel_texts: string[];
  chat_wheel_volumes: number[];
  chat_wheel_last_index: number;
  rolling_buffer_hotkey: string;
  rolling_buffer_seconds: number;
  hold_key_enabled: boolean;
  hold_key: string;
  volume: number;
  overlay_opacity: number;
  overlay_placeholder: string;
  ui_font: string;
  fixed_center: boolean;
  log_level: string;
  main_playback_enabled: boolean;
  audio_output_device_name: string | null;
  audio_output_device: AudioIdentity | null;
  tts_providers: {
    Cartesia: {
      voice_id: string | null;
      voice_name: string | null;
      backend: string;
      api_key_set: boolean;
    };
    Volcengine: { voice_list: Voice[]; backend: string; api_key_set: boolean };
  };
  active_tts_provider: Provider;
  audio_routing_enabled: boolean;
  mic_input_device: string | null;
  sidetone_enabled: boolean;
  additional_chat_wheels: Wheel[];
};

type ListenStatus = { enabled: boolean; output: string | null; output_available: boolean };

export type Snapshot = {
  settings: Settings;
  outputs: AudioIdentity[];
  microphones: string[];
  voices: Voice[];
  cable_available: boolean;
  listen: ListenStatus | null;
  fonts: string[];
  busy: boolean;
  locked: boolean;
  preview: { wheel: number; slot: number; text: boolean } | null;
};

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const BACKENDS: Record<Provider, string[]> = {
  Cartesia: ["Cartesia Bytes", "Cartesia Realtime"],
  Volcengine: ["Volcengine Streaming"],
};

type Tab = "local" | "audio" | "tts" | "wheels" | "rolling";

const TABS: { id: Tab; label: string }[] = [
  { id: "local", label: "本地" },
  { id: "audio", label: "音频" },
  { id: "tts", label: "TTS" },
  { id: "wheels", label: "轮盘" },
  { id: "rolling", label: "后台录制" },
];

export const displayHotkey = (value: string) =>
  value
    .split("+")
    .map((part) =>
      part.length === 1 ? part.toUpperCase() : part.charAt(0).toUpperCase() + part.slice(1),
    )
    .join(" + ");

function providerInfo(settings: Settings, provider: Provider) {
  return provider === "Cartesia"
    ? settings.tts_providers.Cartesia
    : settings.tts_providers.Volcengine;
}

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [tab, setTab] = useState<Tab>("local");
  const [keyInput, setKeyInput] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [voiceName, setVoiceName] = useState("");
  const [placeholder, setPlaceholder] = useState("");
  const [logs, setLogs] = useState<string[] | null>(null);
  const pending = useRef<Record<string, number>>({});

  const apply = useCallback((next: Snapshot) => {
    setSnapshot(next);
    const value = next.settings.overlay_placeholder;
    // 只在后端值真正变化时覆盖正在编辑的输入框。
    setPlaceholder((current) => (current === value ? current : value));
  }, []);

  useEffect(() => {
    let disposed = false;
    invoke<Snapshot>("wordy_snapshot")
      .then((next) => !disposed && apply(next))
      .catch((error) => console.error(error));
    const unlisten = listen<Snapshot>("wordy://snapshot", (event) => apply(event.payload));
    const unlistenLog = listen<string>("wordy://log", (event) =>
      setLogs((current) => (current ? [...current.slice(-4999), event.payload] : current)),
    );
    const unlistenOpen = listen("wordy://open-logs", () => {
      invoke<string[]>("wordy_logs")
        .then(setLogs)
        .catch((error) => console.error(error));
    });
    return () => {
      disposed = true;
      unlisten.then((stop) => stop());
      unlistenLog.then((stop) => stop());
      unlistenOpen.then((stop) => stop());
    };
  }, [apply]);

  const run = useCallback(
    (action: Record<string, unknown>) => {
      invoke<Snapshot>("wordy_action", { action })
        .then(apply)
        .catch((error) => console.error(error));
    },
    [apply],
  );

  const audioRequested = useRef(false);
  useEffect(() => {
    // 设备列表由原生线程枚举；设置页第一次拿到快照后请求一次。
    if (snapshot && !audioRequested.current) {
      audioRequested.current = true;
      run({ type: "request_audio_devices" });
    }
  }, [snapshot, run]);

  const sendDebounced = useCallback(
    (key: string, delay: number, action: Record<string, unknown>) => {
      window.clearTimeout(pending.current[key]);
      pending.current[key] = window.setTimeout(() => run(action), delay);
    },
    [run],
  );

  if (!snapshot) {
    return (
      <main className="flex h-screen items-center justify-center text-sm text-[var(--muted)]">
        正在加载设置…
      </main>
    );
  }

  const { settings } = snapshot;
  const provider = settings.active_tts_provider;
  const info = providerInfo(settings, provider);
  const voices =
    provider === "Cartesia" ? snapshot.voices : settings.tts_providers.Volcengine.voice_list;
  // Volcengine 用列表第一位作为当前音色，下拉框必须回显它，否则选完会跳回「无」。
  const selectedVoice =
    provider === "Cartesia"
      ? settings.tts_providers.Cartesia.voice_id
      : (settings.tts_providers.Volcengine.voice_list[0]?.id ?? null);
  const fontHint = !settings.ui_font
    ? "使用内置字体栈。"
    : snapshot.fonts.includes(settings.ui_font)
      ? `界面使用「${settings.ui_font}」。`
      : `未安装「${settings.ui_font}」，已回退到内置字体。`;

  return (
    <main className="relative mx-auto flex h-screen max-w-[1240px] flex-col px-6 pb-4 pt-5">
      <header className="flex items-end justify-between gap-6">
        <h1 className="pb-2 text-2xl font-semibold tracking-tight">设置</h1>
        <nav className="flex gap-1 rounded-xl bg-white/5 p-1">
          {TABS.map((item) => (
            <button
              key={item.id}
              onClick={() => setTab(item.id)}
              aria-pressed={tab === item.id}
              className={`rounded-lg px-4 py-1.5 text-sm transition ${
                tab === item.id
                  ? "bg-[var(--accent)] text-white"
                  : "text-[var(--muted)] hover:bg-white/8 hover:text-white"
              }`}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </header>

      <div className="mt-4 flex-1 overflow-y-auto pr-1 [scrollbar-gutter:stable]">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {tab === "local" && (
            <>
              <Card title="快捷键与播放">
                <Row label="全局快捷键">
                  <div className="flex gap-2">
                    <output className="field min-w-0 flex-1 truncate">
                      {displayHotkey(settings.hotkey) || "未设置"}
                    </output>
                    <button
                      className="btn icon-btn"
                      title="录制快捷键"
                      aria-label="录制快捷键"
                      onClick={() => run({ type: "record_hotkey" })}
                    >
                      ⌨️
                    </button>
                  </div>
                  <Hint>点击录制后按下新的快捷键组合</Hint>
                </Row>
                <Row label="播放时自动按住某键">
                  <div className="flex flex-wrap items-center gap-3">
                    <Check
                      checked={settings.hold_key_enabled}
                      onChange={(value) => run({ type: "set_hold_key_enabled", value })}
                    />
                    <output className="field min-w-0 flex-1 truncate">
                      {displayHotkey(settings.hold_key) || "未设置"}
                    </output>
                    <button
                      className="btn icon-btn"
                      title="录制按住键"
                      aria-label="录制按住键"
                      onClick={() => run({ type: "record_hold_key" })}
                    >
                      ⌨️
                    </button>
                  </div>
                  <Hint>开启后，播放语音前自动按下该键，结束后松开。</Hint>
                </Row>
                <Slider
                  label="朗读音量"
                  value={settings.volume}
                  min={0.5}
                  max={2}
                  step={0.05}
                  display={`${settings.volume.toFixed(2)}x`}
                  onChange={(value) => run({ type: "set_volume", value })}
                />
                <Hint>范围 0.50x - 2.00x，默认 1.00x</Hint>
              </Card>

              <Card title="界面与显示">
                <Row label="界面字体">
                  <select
                    className="field"
                    value={settings.ui_font}
                    onChange={(event) => run({ type: "set_ui_font", value: event.target.value })}
                  >
                    <option value="">内置字体</option>
                    {snapshot.fonts.map((font) => (
                      <option key={font} value={font}>
                        {font}
                      </option>
                    ))}
                  </select>
                  <Hint>{fontHint}</Hint>
                </Row>
                <Row label="输入框提示文字">
                  <input
                    className="field"
                    value={placeholder}
                    onChange={(event) => {
                      const value = event.target.value;
                      setPlaceholder(value);
                      sendDebounced("placeholder", 250, { type: "set_placeholder", value });
                    }}
                  />
                </Row>
                <Slider
                  label="输入框背景不透明度"
                  value={settings.overlay_opacity}
                  min={0.3}
                  max={1}
                  step={0.01}
                  display={`${Math.round(settings.overlay_opacity * 100)}%`}
                  onChange={(value) => run({ type: "set_opacity", value })}
                />
                <Hint>30% 更透明，100% 完全不透明；文字保持清晰。</Hint>
                <Row label="输入栏位置">
                  <label className="flex items-center gap-2 text-sm">
                    <Check
                      checked={settings.fixed_center}
                      onChange={(value) => run({ type: "set_fixed_center", value })}
                    />
                    固定出现在屏幕中心
                  </label>
                  <Hint>取消勾选后，可拖动输入窗口；松开鼠标后自动记住位置。</Hint>
                </Row>
                <Row label="日志等级">
                  <select
                    className="field"
                    value={settings.log_level}
                    onChange={(event) => run({ type: "set_log_level", value: event.target.value })}
                  >
                    {LOG_LEVELS.map((level) => (
                      <option key={level}>{level}</option>
                    ))}
                  </select>
                  <Hint>设置日志记录的详细程度。</Hint>
                </Row>
              </Card>
            </>
          )}

          {tab === "audio" && (
            <>
              <Card title="播放与返听">
                <Row label="主播放输出">
                  <label className="flex items-center gap-2 text-sm">
                    <Check
                      checked={settings.main_playback_enabled}
                      onChange={(value) => run({ type: "set_main_playback", value })}
                    />
                    启用主播放输出
                  </label>
                  <Hint>
                    {snapshot.cable_available
                      ? "输出至 CABLE Input。语音软件的输入设备选 CABLE Output。"
                      : "未检测到 VB-CABLE。安装后即可输出至 CABLE Input。"}
                  </Hint>
                  {!snapshot.cable_available && (
                    <button className="btn mt-2" onClick={() => run({ type: "download_vb_cable" })}>
                      ⬇️ VB-CABLE
                    </button>
                  )}
                </Row>
                <Row label="返听">
                  <label className="flex items-center gap-2 text-sm">
                    <Check
                      checked={settings.sidetone_enabled}
                      onChange={(value) => run({ type: "set_sidetone", value })}
                    />
                    启用返听
                  </label>
                </Row>
                <Row label="返听播放设备">
                  <select
                    className="field"
                    value={outputIndex(snapshot)}
                    onChange={(event) => {
                      const index = Number(event.target.value);
                      run({
                        type: "set_output_device",
                        device: index > 0 ? snapshot.outputs[index - 1] : null,
                      });
                    }}
                  >
                    <option value={0}>默认（跟随系统）</option>
                    {snapshot.outputs.map((output, index) => (
                      <option key={`${output.id ?? output.name}-${index}`} value={index + 1}>
                        {outputLabel(output)}
                      </option>
                    ))}
                  </select>
                  <Hint>
                    {outputIndex(snapshot) === 0 &&
                    (settings.audio_output_device || settings.audio_output_device_name)
                      ? `已找到 ${snapshot.outputs.length} 个输出设备；已保存的返听设备不可用。`
                      : `已找到 ${snapshot.outputs.length} 个输出设备。`}
                  </Hint>
                </Row>
              </Card>

              <Card title="麦克风与侦听">
                <Row label="音频侦听">
                  <Hint>通过 Windows 将所选麦克风送至 CABLE Input。</Hint>
                </Row>
                <Row label="侦听输出设备">
                  <output className="field">
                    {snapshot.cable_available ? "CABLE Input" : "未检测到 VB-CABLE"}
                  </output>
                </Row>
                <Row>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="check"
                      disabled={!snapshot.cable_available}
                      checked={snapshot.listen?.enabled ?? settings.audio_routing_enabled}
                      onChange={(event) => run({ type: "set_listen", value: event.target.checked })}
                    />
                    启用音频侦听
                  </label>
                </Row>
                <Row label="侦听麦克风">
                  <select
                    className="field"
                    value={micIndex(snapshot)}
                    onChange={(event) => {
                      const index = Number(event.target.value);
                      run({
                        type: "set_mic",
                        name: index > 0 ? snapshot.microphones[index - 1] : null,
                      });
                    }}
                  >
                    <option value={0}>无（不侦听麦克风）</option>
                    {snapshot.microphones.map((mic, index) => (
                      <option key={`${mic}-${index}`} value={index + 1}>
                        {mic}
                      </option>
                    ))}
                  </select>
                  <Hint>{listenStatus(snapshot)}</Hint>
                </Row>
              </Card>
            </>
          )}

          {tab === "tts" && (
            <>
              <Card title="生成方式">
                <Row label="TTS 服务商">
                  <select
                    className="field"
                    value={provider}
                    onChange={(event) => run({ type: "set_provider", provider: event.target.value })}
                  >
                    <option value="Volcengine">Volcengine</option>
                    <option value="Cartesia">Cartesia</option>
                  </select>
                </Row>
                <Row label="生成模式">
                  <select
                    className="field"
                    value={info.backend}
                    onChange={(event) => run({ type: "set_backend", backend: event.target.value })}
                  >
                    {BACKENDS[provider].map((backend) => (
                      <option key={backend}>{backend}</option>
                    ))}
                  </select>
                  <Hint>Bytes：完整生成后播放。Streaming：边生成边播放。</Hint>
                </Row>
              </Card>

              <Card title="密钥与音色">
                <Row label="API Key">
                  <Hint>{info.api_key_set ? "API Key 已保存" : "API Key 未保存"}</Hint>
                  <div className="mt-2 flex gap-2">
                    <input
                      className="field flex-1"
                      type="password"
                      autoComplete="off"
                      value={keyInput}
                      onChange={(event) => setKeyInput(event.target.value)}
                    />
                    <button
                      className="btn icon-btn"
                      title="保存 API Key"
                      aria-label="保存 API Key"
                      onClick={() => {
                        run({ type: "save_key", provider: provider.toLowerCase(), text: keyInput });
                        setKeyInput("");
                      }}
                    >
                      💾
                    </button>
                    <button
                      className="btn icon-btn"
                      title="删除 API Key"
                      aria-label="删除 API Key"
                      disabled={!info.api_key_set}
                      onClick={() => run({ type: "delete_key", provider: provider.toLowerCase() })}
                    >
                      🗑️
                    </button>
                  </div>
                  <Hint>密钥保存在 Windows 凭据管理器中。</Hint>
                </Row>
                <Row label="音色选择">
                  <div className="flex gap-2">
                    <select
                      className="field flex-1"
                      value={voiceSelection(voices, selectedVoice)}
                      onChange={(event) => {
                        const index = Number(event.target.value);
                        const voice = index > 0 ? voices[index - 1] : { id: "", name: "" };
                        run({
                          type: "select_voice",
                          provider: provider.toLowerCase(),
                          voice,
                        });
                      }}
                    >
                      <option value={0}>无</option>
                      {voices.map((voice, index) => (
                        <option key={`${voice.id}-${index}`} value={index + 1}>
                          {voiceLabel(voices, index)}
                        </option>
                      ))}
                    </select>
                    <button
                      className="btn icon-btn"
                      title="刷新音色"
                      aria-label="刷新音色"
                      onClick={() => run({ type: "refresh_voices" })}
                    >
                      🔄
                    </button>
                  </div>
                  <Hint>
                    {provider === "Volcengine"
                      ? `当前 Speaker ID：${settings.tts_providers.Volcengine.voice_list[0]?.id ?? "无"}`
                      : "选择当前用于朗读的音色。"}
                  </Hint>
                </Row>
                {provider === "Volcengine" && (
                  <>
                    <Row label="Speaker ID">
                      <input
                        className="field"
                        value={voiceId}
                        onChange={(event) => setVoiceId(event.target.value)}
                      />
                    </Row>
                    <Row label="名称">
                      <input
                        className="field"
                        value={voiceName}
                        onChange={(event) => setVoiceName(event.target.value)}
                      />
                    </Row>
                    <div className="mt-2 flex gap-2">
                      <button
                        className="btn icon-btn"
                        title="添加 / 保存音色"
                        aria-label="添加 / 保存音色"
                        onClick={() =>
                          run({
                            type: "save_volcengine_voice",
                            voice: { id: voiceId, name: voiceName },
                          })
                        }
                      >
                        💾
                      </button>
                      <button
                        className="btn icon-btn"
                        title="删除音色"
                        aria-label="删除音色"
                        disabled={
                          !(voiceId || selectedVoice) ||
                          !settings.tts_providers.Volcengine.voice_list.some(
                            (voice) => voice.id === (voiceId || selectedVoice),
                          )
                        }
                        onClick={() =>
                          run({ type: "delete_volcengine_voice", id: voiceId || selectedVoice || "" })
                        }
                      >
                        🗑️
                      </button>
                    </div>
                  </>
                )}
              </Card>
            </>
          )}

          {tab === "wheels" && <WheelEditor snapshot={snapshot} run={run} />}

          {tab === "rolling" && (
            <>
              <Card title="录制设置">
                <Row label="后台录制快捷键">
                  <div className="flex gap-2">
                    <output className="field min-w-0 flex-1 truncate">
                      {displayHotkey(settings.rolling_buffer_hotkey) || "未设置"}
                    </output>
                    <button className="btn" onClick={() => run({ type: "record_rolling_hotkey" })}>
                      录制快捷键
                    </button>
                  </div>
                  <Hint>点击录制后按下新的快捷键组合</Hint>
                </Row>
                <Row label="保留时长">
                  <div className="flex items-center gap-2">
                    <input
                      className="field w-28"
                      type="number"
                      min={5}
                      max={300}
                      value={settings.rolling_buffer_seconds}
                      onChange={(event) => {
                        const value = Number(event.target.value);
                        if (Number.isFinite(value)) {
                          sendDebounced("rolling", 300, { type: "set_rolling_seconds", value });
                        }
                      }}
                    />
                    <span className="whitespace-nowrap text-sm text-[var(--muted)]">秒（5–300）</span>
                  </div>
                </Row>
              </Card>
              <Card title="录制与保存">
                <Row label="开始和结束录制">
                  <Hint>按一次快捷键开始录制，再按一次打开保存窗口。</Hint>
                </Row>
                <Row label="保存到轮盘">
                  <Hint>拖选片段，试听后选择轮盘槽位并保存。</Hint>
                </Row>
              </Card>
            </>
          )}
        </div>
      </div>

      {logs && (
        <section className="panel-in absolute inset-x-6 bottom-16 top-16 flex flex-col rounded-2xl border border-white/15 bg-[#17171a]/95 p-4 shadow-2xl backdrop-blur">
          <header className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">日志</h2>
            <button className="btn" onClick={() => setLogs(null)}>
              关闭
            </button>
          </header>
          <ol className="flex-1 select-text overflow-y-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed text-[var(--muted)]">
            {logs.length === 0 && <li>暂无日志</li>}
            {logs.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ol>
        </section>
      )}

      <TrimDialog snapshot={snapshot} run={run} />
      <DialogHost />
      <ToastHost />

      <footer className="mt-3 flex items-center justify-between gap-3 border-t border-white/10 pt-3 text-xs text-[var(--muted)]">
        <span>设置自动保存</span>
        <div className="flex gap-2">
          <button
            className="btn icon-btn"
            title="查看日志"
            aria-label="查看日志"
            onClick={() =>
              invoke<string[]>("wordy_logs")
                .then(setLogs)
                .catch((error) => console.error(error))
            }
          >
            📜
          </button>
          <button
            className="btn"
            disabled={snapshot.busy}
            onClick={() => run({ type: "export_backup" })}
          >
            导出备份
          </button>
          <button
            className="btn"
            disabled={snapshot.busy}
            onClick={() => run({ type: "import_backup" })}
          >
            导入备份
          </button>
          <button
            className="btn icon-btn"
            title="关闭设置"
            aria-label="关闭设置"
            onClick={() => {
              run({ type: "closed" });
              invoke("wordy_hide").catch((error) => console.error(error));
            }}
          >
            ✕
          </button>
        </div>
      </footer>
    </main>
  );
}

function outputLabel(output: AudioIdentity) {
  return output.host_api_name ? `${output.name} (${output.host_api_name})` : output.name;
}

function outputIndex(snapshot: Snapshot) {
  const chosen = snapshot.settings.audio_output_device;
  if (!chosen) return 0;
  const index = snapshot.outputs.findIndex(
    (output) =>
      (chosen.id && output.id === chosen.id) || (!chosen.id && output.name === chosen.name),
  );
  return index >= 0 ? index + 1 : 0;
}

function micIndex(snapshot: Snapshot) {
  const name = snapshot.settings.mic_input_device;
  if (!name) return 0;
  const wanted = name.trim().toLowerCase();
  const index = snapshot.microphones.findIndex((mic) => mic.trim().toLowerCase() === wanted);
  return index >= 0 ? index + 1 : 0;
}

function listenStatus(snapshot: Snapshot) {
  const state = snapshot.listen;
  if (!state) {
    return snapshot.settings.mic_input_device
      ? "无法读取侦听状态——请确认该设备支持系统侦听"
      : "选择麦克风后自动配置";
  }
  let text = state.enabled ? "Windows 侦听：已开启" : "Windows 侦听：未开启";
  if (state.output) text += ` · 输出目标：${short(state.output, 28)}`;
  else if (state.output_available) text += " · 输出目标：设备不可用";
  else if (state.enabled) text += " · 输出目标：默认播放设备";
  else return text;
  const mic = snapshot.settings.mic_input_device;
  const missing =
    mic && !snapshot.microphones.some((item) => item.trim().toLowerCase() === mic.trim().toLowerCase());
  return missing ? `${text} · 已保存的麦克风不可用` : text;
}

function short(text: string, limit: number) {
  const chars = [...text];
  return chars.length <= limit ? text : `${chars.slice(0, limit - 1).join("")}…`;
}

function voiceSelection(voices: Voice[], selected: string | null) {
  const index = voices.findIndex((voice) => voice.id === selected);
  return index >= 0 ? index + 1 : 0;
}

function voiceLabel(voices: Voice[], index: number) {
  const voice = voices[index];
  const duplicate = voices.findIndex((item) => item.name === voice.name) !== index;
  return duplicate ? `${voice.name} (${voice.id.slice(0, 8)})` : voice.name;
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-4 shadow-[0_18px_50px_-30px_rgba(0,0,0,0.9)]">
      <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-wider text-[var(--muted)]">
        {title}
      </h2>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function Row({ label, children }: { label?: string; children: React.ReactNode }) {
  return (
    <div>
      {label && <div className="mb-1.5 text-[13px] text-[var(--text)]">{label}</div>}
      {children}
    </div>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">{children}</p>;
}

function Check({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <input
      type="checkbox"
      className="check"
      checked={checked}
      onChange={(event) => onChange(event.target.checked)}
    />
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  display,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display: string;
  onChange: (value: number) => void;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between text-[13px]">
        <span>{label}</span>
        <span className="tabular-nums text-[var(--muted)]">{display}</span>
      </div>
      <input
        type="range"
        className="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  );
}
