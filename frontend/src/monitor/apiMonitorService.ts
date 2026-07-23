import type { MonitorRun, MonitorSnapshot, MonitorStreamMessage } from "./types";

async function requestSnapshot(): Promise<MonitorSnapshot> {
  const response = await fetch("/api/monitor/snapshot", { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`monitor snapshot request failed: ${response.status}`);
  return response.json() as Promise<MonitorSnapshot>;
}

async function requestRunDetail(runId: string): Promise<MonitorRun> {
  const response = await fetch(`/api/monitor/runs/${encodeURIComponent(runId)}`, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`monitor detail request failed: ${response.status}`);
  return response.json() as Promise<MonitorRun>;
}

function streamUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/monitor/stream`;
}

export const apiMonitorService = {
  getSnapshot: requestSnapshot,
  getRunDetail: requestRunDetail,
  createStream(onUpdate: (message: MonitorStreamMessage) => void) {
    let socket: WebSocket | undefined;
    let reconnectTimer: number | undefined;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(streamUrl());
      socket.addEventListener("open", () => onUpdate({ type: "connection", connected: true }));
      socket.addEventListener("message", (event) => {
        const message = JSON.parse(event.data) as MonitorStreamMessage;
        if (
          message.type === "snapshot"
          || message.type === "run.updated"
          || message.type === "connection"
          || message.type === "upstream.updated"
        ) onUpdate(message);
      });
      socket.addEventListener("close", () => {
        if (stopped) return;
        onUpdate({ type: "connection", connected: false });
        reconnectTimer = window.setTimeout(connect, 1000);
      });
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  },
};
