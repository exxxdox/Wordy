import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
// 与 Windows 打包共用源文件，更新应用图标时会同步更新设置页。
import appIcon from "../../src-tauri/icons/icon.ico?url";
import WheelEditor from "./WheelEditor";
import TrimDialog from "./TrimDialog";
import DialogHost from "./DialogHost";
import ToastHost from "./ToastHost";
import { Icon, Panel, Row, Slider, displayHotkey, useSyncedInput } from "./Controls";

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

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "local", label: "通用", hint: "快捷键、朗读音量与界面显示。" },
  { id: "audio", label: "音频", hint: "主播放输出、返听与麦克风侦听。" },
  { id: "tts", label: "TTS", hint: "服务商、密钥与朗读音色。" },
  { id: "wheels", label: "轮盘", hint: "每个轮盘八个槽位，绑定快捷键后随时调用。" },
  { id: "rolling", label: "后台录制", hint: "循环保留最近一段录音，随时截取保存。" },
];

function providerInfo(settings: Settings, provider: Provider) {
  return provider === "Cartesia"
    ? settings.tts_providers.Cartesia
    : settings.tts_providers.Volcengine;
}

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [tab, setTab] = useState<Tab>("local");
  const [keyInput, setKeyInput] = useState("");
  // 新增音色弹窗的草稿；为 null 时关闭。
  const [voiceDraft, setVoiceDraft] = useState<{ id: string; name: string } | null>(null);
  const [placeholder, setPlaceholder] = useState("");
  const [logs, setLogs] = useState<string[] | null>(null);
  const content = useRef<HTMLDivElement>(null);
  const logsButton = useRef<HTMLButtonElement>(null);
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

  // 设置页、弹窗和截取窗都是 React 渲染，界面字体要同步到 CSS 变量才与原生窗口一致。
  const uiFont = snapshot?.settings.ui_font;
  useEffect(() => {
    const style = document.documentElement.style;
    const targets: Array<[string, string]> = [
      ["--font-ui-configured", "--font-ui-fallback"],
      ["--font-num-configured", "--font-num-fallback"],
    ];
    for (const [name, fallback] of targets) {
      // 字体名缺失时保留 CSS 里的内置栈。
      if (uiFont) style.setProperty(name, `"${uiFont}", var(${fallback})`);
      else style.removeProperty(name);
    }
  }, [uiFont]);

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

  // 数字输入用本地草稿立即回显：快照回来前输入框不会跳回旧值，上下箭头才跟手。
  const rollingSeconds = useSyncedInput(
    snapshot ? String(snapshot.settings.rolling_buffer_seconds) : "",
    (value) => {
      const seconds = Number(value);
      if (Number.isFinite(seconds)) run({ type: "set_rolling_seconds", value: seconds });
    },
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
  const current = TABS.find((item) => item.id === tab) ?? TABS[0];
  const outputChip = !settings.main_playback_enabled
    ? { tone: "", text: "主输出已关闭" }
    : snapshot.cable_available
      ? { tone: "ok", text: "CABLE 就绪" }
      : { tone: "off", text: "CABLE 未安装" };
  const openLogs = () =>
    invoke<string[]>("wordy_logs")
      .then(setLogs)
      .catch((error) => console.error(error));
  const closeLogs = () => {
    setLogs(null);
    logsButton.current?.focus();
  };

  return (
    <main className="app-shell">
      <aside className="rail">
        <div className="brand">
          <img className="brand-mark" src={appIcon} width={40} height={40} alt="" />
          <div>
            <strong>Wordy</strong>
            <span className="brand-caption">设置</span>
          </div>
        </div>
        <nav className="rail-nav" aria-label="设置分类">
          {TABS.map((item) => (
            <button
              key={item.id}
              onClick={() => {
                setTab(item.id);
                // 分区共用滚动容器，切换时从标题后的第一项开始。
                if (item.id !== tab) content.current?.scrollTo(0, 0);
              }}
              aria-current={tab === item.id ? "page" : undefined}
              className="rail-item"
            >
              <Icon name={item.id} size={20} />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="rail-foot">
          <button className="rail-item" ref={logsButton} onClick={openLogs}>
            <Icon name="log" /> 查看日志
          </button>
          <button
            className="rail-item"
            disabled={snapshot.busy}
            onClick={() => run({ type: "import_backup" })}
          >
            <Icon name="import" /> 导入备份
          </button>
          <button
            className="rail-item"
            disabled={snapshot.busy}
            onClick={() => run({ type: "export_backup" })}
          >
            <Icon name="export" /> 导出备份
          </button>
          <p className="rail-note">
            <span className="chip-dot ok" aria-hidden="true" /> 设置自动保存
          </p>
        </div>
      </aside>

      <div className="content">
        <header className="content-head">
          <div className="content-heading">
            <h1 className="content-title">{current.label}</h1>
            <p className="content-sub">{current.hint}</p>
          </div>
          <span className="chip" role="status">
            <span className={`chip-dot ${outputChip.tone}`} aria-hidden="true" />
            {outputChip.text}
          </span>
        </header>

        <div className={tab === "wheels" ? "content-body wheel-grid" : "content-body"} ref={content}>
          {tab === "local" && (
            <>
              <Panel title="快捷键与播放" icon="local">
                <Row label="全局快捷键" hint="打开输入框，输入要朗读的文字。">
                  <div className="hotkey-control">
                    <kbd>{displayHotkey(settings.hotkey) || "未设置"}</kbd>
                    <button
                      className="btn record-btn"
                      aria-label="录制快捷键"
                      onClick={() => run({ type: "record_hotkey" })}
                    >
                      录制
                    </button>
                  </div>
                </Row>
                <Row label="播放时自动按住某键" htmlFor="hold-key" hint="播放前按下此键，结束后自动松开。">
                  <div className="hotkey-control">
                    <kbd>{displayHotkey(settings.hold_key) || "未设置"}</kbd>
                    <button
                      className="btn record-btn"
                      aria-label="录制按住键"
                      onClick={() => run({ type: "record_hold_key" })}
                    >
                      录制
                    </button>
                    <Check
                      id="hold-key"
                      checked={settings.hold_key_enabled}
                      onChange={(value) => run({ type: "set_hold_key_enabled", value })}
                    />
                  </div>
                </Row>
                <Slider
                  label="朗读音量"
                  hint="范围 0.50x–2.00x，默认 1.00x。"
                  value={settings.volume}
                  min={0.5}
                  max={2}
                  step={0.05}
                  display={`${settings.volume.toFixed(2)}x`}
                  onChange={(value) => run({ type: "set_volume", value })}
                />
              </Panel>

              <Panel title="界面与显示" icon="display">
                <Row label="界面字体" htmlFor="ui-font" hint={fontHint}>
                  <select
                    id="ui-font"
                    className="field"
                    value={settings.ui_font}
                    onChange={(event) => run({ type: "set_ui_font", value: event.target.value })}
                  >
                    <option value="">内置字体</option>
                    {snapshot.fonts.map((font) => (
                      <option key={font} value={font}>{font}</option>
                    ))}
                  </select>
                </Row>
                <Row label="输入框提示文字" htmlFor="placeholder" hint="输入框为空时显示的文字。">
                  <input
                    id="placeholder"
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
                  hint="只调整背景透明度，文字保持清晰。"
                  value={settings.overlay_opacity}
                  min={0.3}
                  max={1}
                  step={0.01}
                  display={`${Math.round(settings.overlay_opacity * 100)}%`}
                  onChange={(value) => run({ type: "set_opacity", value })}
                />
                <Row label="固定出现在屏幕中心" htmlFor="fixed-center" hint="关闭后可拖动输入框，并记住上次位置。">
                  <div className="switch-control">
                    <Check
                      id="fixed-center"
                      checked={settings.fixed_center}
                      onChange={(value) => run({ type: "set_fixed_center", value })}
                    />
                  </div>
                </Row>
                <Row label="日志等级" htmlFor="log-level" hint="调整日志记录的详细程度。">
                  <select
                    id="log-level"
                    className="field"
                    value={settings.log_level}
                    onChange={(event) => run({ type: "set_log_level", value: event.target.value })}
                  >
                    {LOG_LEVELS.map((level) => <option key={level}>{level}</option>)}
                  </select>
                </Row>
              </Panel>
            </>
          )}

          {tab === "audio" && (
            <>
              <Panel title="播放与返听" icon="audio">
                <Row
                  label="启用主播放输出"
                  htmlFor="main-playback"
                  hint={snapshot.cable_available
                    ? "输出至 CABLE Input。语音软件的输入设备请选择 CABLE Output。"
                    : "未检测到 VB-CABLE。安装后即可使用主播放输出。"}
                >
                  <div className="switch-control">
                    {!snapshot.cable_available && (
                      <button className="btn" onClick={() => run({ type: "download_vb_cable" })}>
                        下载 VB-CABLE
                      </button>
                    )}
                    <Check
                      id="main-playback"
                      checked={settings.main_playback_enabled}
                      onChange={(value) => run({ type: "set_main_playback", value })}
                    />
                  </div>
                </Row>
                <Row label="启用返听" htmlFor="sidetone" hint="同时在本机播放，以便听到自己的输出。">
                  <div className="switch-control">
                    <Check
                      id="sidetone"
                      checked={settings.sidetone_enabled}
                      onChange={(value) => run({ type: "set_sidetone", value })}
                    />
                  </div>
                </Row>
                <Row
                  label="返听播放设备"
                  htmlFor="output-device"
                  hint={outputIndex(snapshot) === 0 &&
                    (settings.audio_output_device || settings.audio_output_device_name)
                      ? "已保存的返听设备不可用。"
                      : `已找到 ${snapshot.outputs.length} 个输出设备。`}
                >
                  <select
                    id="output-device"
                    className="field"
                    value={outputIndex(snapshot)}
                    onChange={(event) => {
                      const index = Number(event.target.value);
                      run({ type: "set_output_device", device: index > 0 ? snapshot.outputs[index - 1] : null });
                    }}
                  >
                    <option value={0}>默认（跟随系统）</option>
                    {snapshot.outputs.map((output, index) => (
                      <option key={`${output.id ?? output.name}-${index}`} value={index + 1}>
                        {outputLabel(output)}
                      </option>
                    ))}
                  </select>
                </Row>
              </Panel>

              <Panel title="麦克风与侦听" icon="mic">
                <Row label="启用音频侦听" htmlFor="listen" hint={listenStatus(snapshot)}>
                  <div className="switch-control">
                    <Check
                      id="listen"
                      disabled={!snapshot.cable_available}
                      checked={snapshot.listen?.enabled ?? settings.audio_routing_enabled}
                      onChange={(value) => run({ type: "set_listen", value })}
                    />
                  </div>
                </Row>
                <Row label="侦听麦克风" htmlFor="microphone" hint="将所选麦克风的声音通过 Windows 送至 CABLE Input。">
                  <select
                    id="microphone"
                    className="field"
                    value={micIndex(snapshot)}
                    onChange={(event) => {
                      const index = Number(event.target.value);
                      run({ type: "set_mic", name: index > 0 ? snapshot.microphones[index - 1] : null });
                    }}
                  >
                    <option value={0}>无（不侦听麦克风）</option>
                    {snapshot.microphones.map((mic, index) => (
                      <option key={`${mic}-${index}`} value={index + 1}>{mic}</option>
                    ))}
                  </select>
                </Row>
                <Row label="侦听输出设备">
                  <output className="device-output">
                    {snapshot.cable_available ? "CABLE Input" : "未检测到 VB-CABLE"}
                  </output>
                </Row>
              </Panel>
            </>
          )}

          {tab === "tts" && (
            <>
              <Panel title="生成方式" icon="tts">
                <Row label="TTS 服务商" htmlFor="tts-provider">
                  <select
                    id="tts-provider"
                    className="field"
                    value={provider}
                    onChange={(event) => run({ type: "set_provider", provider: event.target.value })}
                  >
                    <option value="Volcengine">Volcengine</option>
                    <option value="Cartesia">Cartesia</option>
                  </select>
                </Row>
                <Row label="生成模式" htmlFor="tts-backend" hint="Bytes 完整生成后播放，流式模式边生成边播放。">
                  <select
                    id="tts-backend"
                    className="field"
                    value={info.backend}
                    onChange={(event) => run({ type: "set_backend", backend: event.target.value })}
                  >
                    {BACKENDS[provider].map((backend) => (
                      <option key={backend}>{backend}</option>
                    ))}
                  </select>
                </Row>
              </Panel>

              <Panel title="密钥与音色" icon="key">
                <Row
                  label="API Key"
                  htmlFor="api-key"
                  hint={info.api_key_set ? "已保存到 Windows 凭据管理器。" : "尚未保存密钥。"}
                >
                  <div className="flex gap-2">
                    <input
                      id="api-key"
                      className="field flex-1"
                      type="password"
                      autoComplete="off"
                      value={keyInput}
                      onChange={(event) => setKeyInput(event.target.value)}
                    />
                    <button
                      className="btn primary"
                      onClick={() => {
                        run({ type: "save_key", provider: provider.toLowerCase(), text: keyInput });
                        setKeyInput("");
                      }}
                    >
                      保存
                    </button>
                    <button
                      className="btn ghost"
                      disabled={!info.api_key_set}
                      onClick={() => run({ type: "delete_key", provider: provider.toLowerCase() })}
                    >
                      删除
                    </button>
                  </div>
                </Row>
                <Row
                  label="音色选择"
                  htmlFor="voice"
                  hint={provider === "Volcengine"
                    ? `当前 Speaker ID：${selectedVoice ?? "无"}`
                    : "选择当前用于朗读的音色。"}
                >
                  <div className="flex gap-2">
                    <select
                      id="voice"
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
                    {provider === "Volcengine" ? (
                      // 删除只作用于下拉框中当前选中的音色；未选中时禁用。
                      <button
                        className="btn icon-btn danger"
                        title="删除当前音色"
                        aria-label="删除当前音色"
                        disabled={!selectedVoice}
                        onClick={() =>
                          run({ type: "delete_volcengine_voice", id: selectedVoice ?? "" })
                        }
                      >
                        <Icon name="close" size={18} />
                      </button>
                    ) : (
                      <button
                        className="btn icon-btn"
                        title="刷新音色"
                        aria-label="刷新音色"
                        onClick={() => run({ type: "refresh_voices" })}
                      >
                        <Icon name="refresh" />
                      </button>
                    )}
                  </div>
                  {provider === "Volcengine" && (
                    <button className="btn mt-2" onClick={() => setVoiceDraft({ id: "", name: "" })}>
                      新增音色
                    </button>
                  )}
                </Row>
              </Panel>
            </>
          )}

          {tab === "wheels" && <WheelEditor snapshot={snapshot} run={run} />}

          {tab === "rolling" && (
            <>
              <Panel title="录制设置" icon="rolling">
                <Row label="后台录制快捷键" hint="用于开始录制或打开截取窗口。">
                  <div className="hotkey-control">
                    <kbd>{displayHotkey(settings.rolling_buffer_hotkey) || "未设置"}</kbd>
                    <button
                      className="btn record-btn"
                      aria-label="录制后台录制快捷键"
                      onClick={() => run({ type: "record_rolling_hotkey" })}
                    >
                      录制
                    </button>
                  </div>
                </Row>
                <Row label="保留时长" htmlFor="rolling-seconds" hint="保留最近一段录音，可设为 5–300 秒。">
                  <div className="flex items-center gap-2">
                    <input
                      id="rolling-seconds"
                      ref={rollingSeconds.ref}
                      className="field w-28"
                      type="number"
                      min={5}
                      max={300}
                      value={rollingSeconds.draft}
                      onChange={(event) => rollingSeconds.onChange(event.target.value)}
                      onBlur={rollingSeconds.flush}
                    />
                    <span className="whitespace-nowrap text-sm text-[var(--muted)]">秒</span>
                  </div>
                </Row>
              </Panel>
              <Panel title="录制与保存" icon="log">
                <Row label="开始和结束录制" hint="按一次快捷键开始录制，再按一次打开截取窗口。" />
                <Row label="保存到轮盘" hint="拖选片段，试听后选择轮盘槽位并保存。" />
              </Panel>
            </>
          )}
        </div>

        <footer className="content-foot">
          <span>关闭窗口后在托盘继续运行</span>
          <button
            className="btn"
            onClick={() => {
              run({ type: "closed" });
              invoke("wordy_hide").catch((error) => console.error(error));
            }}
          >
            关闭设置
          </button>
        </footer>
      </div>

      {logs && (
        <div
          className="overlay"
          role="dialog"
          aria-label="日志"
          onKeyDown={(event) => {
            if (event.key === "Escape") closeLogs();
          }}
        >
          <section className="overlay-card panel-in flex h-[calc(100%-3rem)] w-[calc(100%-3rem)] max-w-[860px] flex-col p-4">
            <header className="panel-head">
              <h2 className="panel-title flex items-center gap-2">
                <Icon name="log" /> 日志
              </h2>
              <div className="flex gap-2">
                <button className="btn sm ghost" onClick={openLogs}>
                  刷新
                </button>
                <button
                  className="btn icon-btn"
                  title="关闭日志"
                  aria-label="关闭日志"
                  autoFocus
                  onClick={closeLogs}
                >
                  <Icon name="close" />
                </button>
              </div>
            </header>
            <ol className="log-list" tabIndex={0} aria-label="日志内容">
              {logs.length === 0 && <li>暂无日志</li>}
              {logs.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ol>
          </section>
        </div>
      )}

      {voiceDraft && (
        <div
          className="overlay"
          onKeyDown={(event) => {
            if (event.key === "Escape") setVoiceDraft(null);
          }}
        >
          <form
            role="dialog"
            aria-modal="true"
            aria-label="新增音色"
            className="overlay-card panel-in w-[26rem] p-5"
            onSubmit={(event) => {
              event.preventDefault();
              const id = voiceDraft.id.trim();
              if (!id) return;
              run({ type: "save_volcengine_voice", voice: { id, name: voiceDraft.name.trim() } });
              setVoiceDraft(null);
            }}
          >
            <h2 className="text-sm font-semibold">新增音色</h2>
            <label className="mt-3 block text-sm">
              Speaker ID
              <input
                autoFocus
                className="field mt-1.5"
                value={voiceDraft.id}
                onChange={(event) => setVoiceDraft({ ...voiceDraft, id: event.target.value })}
              />
            </label>
            <label className="mt-3 block text-sm">
              名称
              <input
                className="field mt-1.5"
                value={voiceDraft.name}
                onChange={(event) => setVoiceDraft({ ...voiceDraft, name: event.target.value })}
              />
            </label>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className="btn" onClick={() => setVoiceDraft(null)}>
                取消
              </button>
              <button className="btn primary" disabled={!voiceDraft.id.trim()}>
                添加
              </button>
            </div>
          </form>
        </div>
      )}

      <TrimDialog snapshot={snapshot} run={run} />
      <DialogHost />
      <ToastHost />
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

function Check({
  id,
  disabled,
  checked,
  onChange,
}: {
  id: string;
  disabled?: boolean;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <input
      id={id}
      type="checkbox"
      role="switch"
      className="check"
      disabled={disabled}
      checked={checked}
      onChange={(event) => onChange(event.target.checked)}
    />
  );
}
