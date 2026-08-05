import { describe, expect, it } from "vitest";
import { buildExclusionSearchTree } from "./DashboardCockpit";
import type { DashboardRow } from "@/types/dashboard";

function row(
  id: number,
  nodeType: DashboardRow["node_type"],
  parentId: number | null,
  nodeName: string,
): DashboardRow {
  return {
    id,
    node_code: `N${id}`,
    node_name: nodeName,
    node_type: nodeType,
    level_no: 1,
    parent_id: parentId,
    collection_run_id: null,
    collected_at: null,
    metrics: {},
  };
}

describe("channel exclusion search tree", () => {
  it("keeps only matched channels and their ancestor path", () => {
    const city = row(1, "CITY", null, "郑州市");
    const branch = row(2, "BRANCH", 1, "中原区分公司");
    const grid = row(3, "GRID", 2, "建设路网格");
    const manager = row(4, "CHANNEL_MANAGER", 3, "渠道经理甲");
    const matchedChannel = row(5, "CHANNEL", 4, "爱家营业厅");
    const siblingChannel = row(6, "CHANNEL", 4, "其他营业厅");

    const tree = buildExclusionSearchTree(
      [matchedChannel],
      [city, branch, grid, manager, siblingChannel],
      1,
    );

    expect(tree.rootIds).toEqual([1]);
    expect(tree.childrenByParentId).toEqual({
      1: [2],
      2: [3],
      3: [4],
      4: [5],
    });
    expect(Object.keys(tree.nodesById).map(Number).sort((left, right) => left - right)).toEqual([
      1, 2, 3, 4, 5,
    ]);
    expect(tree.matchingChannelIds.has(5)).toBe(true);
    expect(tree.matchingChannelIds.has(6)).toBe(false);
  });
});
