const SHANGHAI = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function formatShanghai(value: string | null | undefined): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : SHANGHAI.format(date);
}

export function percent(value: number | null): string {
  return value === null ? "-" : `${value.toFixed(1)}%`;
}
