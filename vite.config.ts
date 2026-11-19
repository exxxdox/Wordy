import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Tauri 自己输出编译错误，Vite 清屏会覆盖它们。
  clearScreen: false,
  server: {
    // 显式绑定 IPv4：WebView2 解析 localhost 时可能先试 IPv6。
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
    watch: { ignored: ["**/src-tauri/**"] },
  },
  build: {
    outDir: "dist-ui",
    emptyOutDir: true,
  },
});
