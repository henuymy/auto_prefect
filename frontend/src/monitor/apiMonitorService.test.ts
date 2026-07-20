import { describe, expect, it, vi } from "vitest";
import { apiMonitorService } from "./apiMonitorService";


class FakeWebSocket {
  static instance: FakeWebSocket | undefined;
  static instances: FakeWebSocket[] = [];
  private listeners = new Map<string, (event: Event | MessageEvent<string>) => void>();

  constructor() {
    FakeWebSocket.instance = this;
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, listener: (event: Event | MessageEvent<string>) => void) {
    this.listeners.set(type, listener);
  }

  close() {}

  emit(payload: object) {
    this.listeners.get("message")?.({ data: JSON.stringify(payload) } as MessageEvent<string>);
  }

  emitClose() {
    this.listeners.get("close")?.({} as Event);
  }
}


describe("apiMonitorService", () => {
  it("forwards run.updated WebSocket messages", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const onUpdate = vi.fn();

    const stop = apiMonitorService.createStream(onUpdate);
    FakeWebSocket.instance?.emit({
      type: "run.updated",
      runId: "run-1",
      run: null,
      pendingQueue: { scopeLabel: "通报当前 Scheduled", total: 0, items: [] },
      summary: { succeeded: 1, running: 0, failed: 0, scheduled: 0 },
      updatedAt: "2026-07-19T09:00:01+08:00",
      connected: true,
    });

    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({
      type: "run.updated",
      runId: "run-1",
    }));
    stop();
    vi.unstubAllGlobals();
  });

  it("reports a disconnect and reconnects after the socket closes", () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeWebSocket);
    FakeWebSocket.instances = [];
    const onUpdate = vi.fn();

    const stop = apiMonitorService.createStream(onUpdate);
    FakeWebSocket.instance?.emitClose();

    expect(onUpdate).toHaveBeenCalledWith({ type: "connection", connected: false });
    vi.advanceTimersByTime(1000);
    expect(FakeWebSocket.instances).toHaveLength(2);

    stop();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
});
