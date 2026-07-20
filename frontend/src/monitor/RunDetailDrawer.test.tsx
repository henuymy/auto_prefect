import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RunDetailDrawer } from "./RunDetailDrawer";
import { monitorRuns } from "./mockMonitorService";

describe("RunDetailDrawer", () => {
  it("uses dialog semantics, moves focus inside, and closes with Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<RunDetailDrawer run={monitorRuns[0]} onClose={onClose} />);

    const drawer = screen.getByRole("dialog", { name: "运行详情" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "关闭运行详情" }));

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(drawer.getAttribute("aria-modal")).toBe("true");
  });

  it("shows a failed run summary in the default business overview", () => {
    const view = render(<RunDetailDrawer run={{ ...monitorRuns[0], detailAvailable: false, detailMessage: "Prefect 详情暂不可用，正在显示已同步摘要。" }} onClose={vi.fn()} />);
    const scoped = within(view.container);

    const failureSummary = scoped.getByLabelText("失败摘要");
    expect(within(failureSummary).getByText("失败摘要")).toBeTruthy();
    expect(within(failureSummary).getByText("登录状态失效，任务未完成")).toBeTruthy();
    expect(scoped.getByText("Prefect 详情暂不可用，正在显示已同步摘要。")).toBeTruthy();
  });

  it("announces the selected information level", async () => {
    const user = userEvent.setup();
    const view = render(<RunDetailDrawer run={monitorRuns[0]} onClose={vi.fn()} />);

    const scoped = within(view.container);
    const business = scoped.getByRole("button", { name: "业务概览" });
    const technical = scoped.getByRole("button", { name: "技术详情" });
    expect(business.getAttribute("aria-pressed")).toBe("true");
    expect(technical.getAttribute("aria-pressed")).toBe("false");

    await user.click(technical);
    expect(business.getAttribute("aria-pressed")).toBe("false");
    expect(technical.getAttribute("aria-pressed")).toBe("true");
  });
  it("keeps the internal run identifier out of the business overview", async () => {
    const user = userEvent.setup();
    const view = render(<RunDetailDrawer run={monitorRuns[0]} onClose={vi.fn()} />);
    const scoped = within(view.container);

    expect(scoped.queryByText(monitorRuns[0].id)).toBeNull();
    await user.click(scoped.getByRole("button", { name: "技术详情" }));
    expect(scoped.getByText(monitorRuns[0].id)).toBeTruthy();
  });

  it("translates a generic Prefect startup failure in the business overview", () => {
    const rawSummary = "Flow run could not start: unhandled errors in a TaskGroup (1 sub-exception)";
    const run = {
      ...monitorRuns[0],
      currentStep: "运行失败",
      error: { ...monitorRuns[0].error!, failedStep: "运行失败", businessSummary: rawSummary, technicalSummary: rawSummary },
    };
    const view = render(<RunDetailDrawer run={run} onClose={vi.fn()} />);
    const scoped = within(view.container);

    expect(scoped.getByText("调度服务未能启动本次通报，未开始执行。")).toBeTruthy();
    expect(scoped.getByText("失败步骤：调度初始化")).toBeTruthy();
    expect(scoped.queryByText(rawSummary)).toBeNull();
  });
  it("cycles Tab focus within the detail drawer", async () => {
    const user = userEvent.setup();
    const view = render(<RunDetailDrawer run={monitorRuns[0]} onClose={vi.fn()} />);
    const scoped = within(view.container);
    const close = scoped.getByRole("button", { name: "关闭运行详情" });
    const technical = scoped.getByRole("button", { name: "技术详情" });

    technical.focus();
    await user.tab();
    expect(document.activeElement).toBe(close);

    await user.tab({ shift: true });
    expect(document.activeElement).toBe(technical);
  });
});
