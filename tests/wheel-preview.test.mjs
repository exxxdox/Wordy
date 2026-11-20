import assert from "node:assert/strict";
import { test } from "node:test";
import { watchWheelPreview } from "../src/ui/wheel-preview.ts";

test("wheel preview coalesces frames and paints only the latest selection", async (t) => {
  const logError = t.mock.method(console, "error", () => {});
  let observer;
  let observations = 0;
  globalThis.ResizeObserver = class {
    constructor(callback) { observer = callback; }
    observe() { observations++; }
    disconnect() {}
  };
  const frames = new Map();
  let frameId = 0;
  globalThis.window = {
    requestAnimationFrame(callback) {
      const id = ++frameId;
      frames.set(id, callback);
      return id;
    },
    cancelAnimationFrame(id) { frames.delete(id); },
  };
  const runFrame = () => {
    assert.equal(frames.size, 1);
    const callback = frames.values().next().value;
    frames.clear();
    callback();
  };
  let request = { labels: ["北"], selected: 0, font: "", scale: 1 };
  const submitted = [];
  const painted = [];
  const preview = watchWheelPreview({}, () => request, (input) => {
    const pending = Promise.withResolvers();
    submitted.push({ input, ...pending });
    return pending.promise;
  }, (buffer) => painted.push(buffer));

  preview.update();
  observer();
  runFrame();
  request = { ...request, selected: 1 };
  preview.update();
  request = { ...request, selected: 2, scale: 2 };
  observer();
  assert.equal(submitted.length, 1);
  assert.equal(frames.size, 0);
  assert.equal(observations, 1);

  submitted[0].resolve(new ArrayBuffer(8));
  await new Promise(setImmediate);
  assert.deepEqual(painted, []);
  runFrame();
  assert.deepEqual(submitted.map(({ input }) => input.selected), [0, 2]);
  assert.equal(submitted[1].input.scale, 2);

  const latest = new ArrayBuffer(8);
  submitted[1].resolve(latest);
  await new Promise(setImmediate);
  assert.deepEqual(painted, [latest]);
  request = { ...request, labels: [...request.labels] };
  preview.update();
  assert.equal(frames.size, 0);

  request = { ...request, selected: 3 };
  preview.update();
  runFrame();
  submitted[2].reject(new Error("renderer failed"));
  await new Promise(setImmediate);
  assert.equal(logError.mock.callCount(), 1);
  assert.equal(frames.size, 0);

  preview.update();
  runFrame();
  preview.dispose();
  submitted[3].resolve(new ArrayBuffer(8));
  await new Promise(setImmediate);
  assert.deepEqual(painted, [latest]);
  assert.equal(frames.size, 0);
});
