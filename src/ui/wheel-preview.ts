type Preview = {
  labels: string[];
  selected?: number | null;
  font: string;
  scale: number;
};

export function watchWheelPreview(
  element: HTMLCanvasElement,
  read: () => Preview | null,
  render: (preview: Preview) => Promise<ArrayBuffer>,
  paint: (buffer: ArrayBuffer) => void,
) {
  let frame: number | null = null;
  let disposed = false;
  let rendering = false;
  let drawn: string | undefined;
  let desired: { preview: Preview; key: string } | null = null;

  // 原生位图较大，等当前请求结束后只补画最新输入。
  const schedule = () => {
    if (disposed || frame !== null || rendering || !desired || desired.key === drawn) return;
    frame = window.requestAnimationFrame(() => {
      frame = null;
      void draw();
    });
  };

  const draw = async () => {
    const next = desired;
    if (!next || next.key === drawn) return;
    rendering = true;
    try {
      const buffer = await render(next.preview);
      if (!disposed && desired?.key === next.key) {
        paint(buffer);
        drawn = next.key;
      }
    } catch (error) {
      if (!disposed) console.error(error);
      // 避免同一个失败请求每帧自动重试，交给下一次输入或尺寸变化触发。
      if (desired?.key === next.key) desired = null;
    } finally {
      rendering = false;
      schedule();
    }
  };

  const update = () => {
    if (disposed) return;
    const preview = read();
    desired = preview ? { preview, key: JSON.stringify(preview) } : null;
    schedule();
  };
  const observer = new ResizeObserver(update);
  observer.observe(element);

  return {
    update,
    dispose() {
      disposed = true;
      if (frame !== null) window.cancelAnimationFrame(frame);
      observer.disconnect();
    },
  };
}
