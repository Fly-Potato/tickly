import {
  parseAbsolute,
  parseDateTime,
  toZoned,
  type ZonedDateTime,
} from "@internationalized/date"

export class TaskTimeError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "TaskTimeError"
  }
}

function pad(value: number): string {
  return String(value).padStart(2, "0")
}

function wallValue(value: ZonedDateTime): string {
  return `${value.year}-${pad(value.month)}-${pad(value.day)}T${pad(value.hour)}:${pad(value.minute)}`
}

export function toDateTimeLocalValue(utcIso: string, timeZone: string): string {
  return wallValue(parseAbsolute(utcIso, timeZone))
}

export function toUtcDueAt(value: string, timeZone: string): string | null {
  if (value === "") {
    return null
  }
  try {
    const wallTime = parseDateTime(value)
    const earlier = toZoned(wallTime, timeZone, "earlier")
    const later = toZoned(wallTime, timeZone, "later")
    // 跳时的不存在时间无法往返为原墙上时间；重复时间则两个候选都能往返。
    if (wallValue(earlier) !== value || wallValue(later) !== value) {
      throw new TaskTimeError("该截止时间因夏令时切换而不存在")
    }
    return earlier.toAbsoluteString()
  } catch (error) {
    if (error instanceof TaskTimeError) {
      throw error
    }
    throw new TaskTimeError("截止时间格式无效")
  }
}

function dateKey(utcIso: string, timeZone: string): string {
  const value = parseAbsolute(utcIso, timeZone)
  return `${value.year}-${pad(value.month)}-${pad(value.day)}`
}

export function formatDueLabel(
  dueAt: string,
  timeZone: string,
  now = new Date(),
): string {
  const formatter = new Intl.DateTimeFormat("zh-CN", {
    timeZone,
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
  const prefix =
    Date.parse(dueAt) < now.getTime()
      ? "已逾期"
      : dateKey(dueAt, timeZone) === dateKey(now.toISOString(), timeZone)
        ? "今天"
        : "截止"
  return `${prefix} · ${formatter.format(new Date(dueAt))}`
}
