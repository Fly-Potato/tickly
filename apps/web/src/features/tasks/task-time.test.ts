import { describe, expect, it } from "vitest"

import {
  TaskTimeError,
  formatDueLabel,
  toDateTimeLocalValue,
  toUtcDueAt,
} from "./task-time"

describe("账号时区截止时间", () => {
  it("按账号时区在 UTC 与 datetime-local 间转换", () => {
    expect(
      toDateTimeLocalValue("2026-07-30T10:00:00Z", "Asia/Shanghai"),
    ).toBe("2026-07-30T18:00")
    expect(toUtcDueAt("2026-07-30T18:00", "Asia/Shanghai")).toBe(
      "2026-07-30T10:00:00.000Z",
    )
    expect(toUtcDueAt("", "Asia/Shanghai")).toBeNull()
  })

  it("使用账号时区处理跨日而不是浏览器时区", () => {
    expect(
      toDateTimeLocalValue("2026-07-30T23:30:00Z", "Asia/Shanghai"),
    ).toBe("2026-07-31T07:30")
  })

  it("拒绝跳时且为重复时间选择较早时刻", () => {
    expect(() =>
      toUtcDueAt("2026-03-08T02:30", "America/Los_Angeles"),
    ).toThrow(TaskTimeError)
    expect(toUtcDueAt("2026-11-01T01:30", "America/Los_Angeles")).toBe(
      "2026-11-01T08:30:00.000Z",
    )
  })

  it("以账号时区生成明确的今天和逾期文本", () => {
    const now = new Date("2026-07-30T10:30:00Z")

    expect(
      formatDueLabel("2026-07-30T12:00:00Z", "Asia/Shanghai", now),
    ).toContain("今天")
    expect(
      formatDueLabel("2026-07-30T09:00:00Z", "Asia/Shanghai", now),
    ).toContain("已逾期")
  })

  it("无效墙上时间转换为稳定字段错误", () => {
    expect(() => toUtcDueAt("not-a-date", "Asia/Shanghai")).toThrow(
      new TaskTimeError("截止时间格式无效"),
    )
  })
})
