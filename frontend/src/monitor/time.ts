const chinaDateTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

export function formatMonitorDateTime(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "—";
  const parts = new Map(chinaDateTime.formatToParts(date).map((part) => [part.type, part.value]));
  return `${parts.get("year")}-${parts.get("month")}-${parts.get("day")} ${parts.get("hour")}:${parts.get("minute")}:${parts.get("second")}`;
}

export function formatMonitorTime(value?: string): string {
  return formatMonitorDateTime(value).slice(-8);
}

export function formatMonitorAge(value: string | undefined, referenceNow = new Date()): string | null {
  if (!value) return null;
  const timestamp = new Date(value).getTime();
  const now = referenceNow.getTime();
  if (!Number.isFinite(timestamp) || !Number.isFinite(now)) return null;
  const seconds = Math.floor((now - timestamp) / 1000);
  if (seconds < 0) return null;
  if (seconds < 60) return `${seconds} 秒前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}