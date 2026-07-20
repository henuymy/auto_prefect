import type { MonitorError } from "./types";

const STARTUP_FAILURE_MARKERS = [
  "flow run could not start",
  "unhandled errors in a taskgroup",
];

const STARTUP_FAILURE_SUMMARY = "调度服务未能启动本次通报，未开始执行。";

function isStartupFailureSummary(summary: string) {
  const normalized = summary.toLowerCase();
  return STARTUP_FAILURE_MARKERS.some((marker) => normalized.includes(marker));
}

export function presentMonitorFailure(error: MonitorError): MonitorError {
  if (!isStartupFailureSummary(error.businessSummary)) return error;

  return {
    ...error,
    failedStep: "调度初始化",
    businessSummary: STARTUP_FAILURE_SUMMARY,
  };
}

export function presentMonitorCurrentStep(currentStep: string, error?: MonitorError) {
  return currentStep === "运行失败" && error && isStartupFailureSummary(error.businessSummary)
    ? "调度初始化"
    : currentStep;
}