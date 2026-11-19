import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 端口由 wordy.ps1 选择后通过环境变量传入；单独运行 pnpm dev 时回落到 1420。
const devPort = Number(process.env.WORDY_DEV_PORT ?? 1420);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Tauri 自己输出编译错误，Vite 清屏会覆盖它们。
  // 首次启动时 Tailwind 的 oxide 扫描可能耗时数十秒，之后 CSS 请求约 1–3 秒。
  clearScreen: false,
  server: {
    // 显式绑定 IPv4：WebView2 解析 localhost 时可能先试 IPv6。
    host: "127.0.0.1",
    port: devPort,
    strictPort: true,
    // 根目录还留着迁移前的 target（数 GB）；不忽略会让 Vite 启动时递归扫描。
    // 启动时预先转换设置页入口，避免第一次打开设置才现场编译模块图。
    warmup: { clientFiles: ["./src/ui/main.tsx"] },
    watch: {
      ignored: ["**/src-tauri/**", "**/target/**", "**/dist/**", "**/dist-ui/**", "**/.codegraph/**", "**/tmp/**"],
    },
  },
  build: {
    outDir: "dist-ui",
    emptyOutDir: true,
  },
});
