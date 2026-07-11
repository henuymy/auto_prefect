import type { ReportConfig, RunLog, ValidationIssue } from "@/types/config";

export const mockConfigs: ReportConfig[] = [
  {
    id: "aj-family-daily",
    name: "爱家亲情网每日盯控",
    template_path: "templates/爱家亲情网每日盯控表.xlsx",
    enabled: true,
    description: "每日抓取智慧运营导出数据，比对后生成通报并发送企微。",
    lastRun: "success",
    updatedAt: "2026-05-12 18:10:23",
    downloads: [
      {
        name: "实时",
        stage: "smart_ops",
        auth_preset: "智慧运营 User-Info",
        method: "POST",
        url: "https://usm.ha.cmcc:19011/zhyypt/smop/export/exportData",
        headers: {
          Accept: "application/json, text/plain, */*",
          "Content-Type": "application/json",
          Origin: "https://usm.ha.cmcc:19011",
          Referer: "https://usm.ha.cmcc:19011/zhyypt/dist/",
        },
        body_type: "json",
        response_mode: "file",
        headers_from_session_storage: {
          "User-Info": "zhyyptInfo.accessToken",
        },
        data: {
          areaId: "AQ",
          dataLevel: "4",
          dataType: "real",
          queryDate: "${today_yyyymmdd}",
          expTypeId: "HOLE_REAL_TC_EXP",
          expFileName: "运营总览-业务销量.xlsx",
          rankDown: null,
        },
      },
      {
        name: "日累计",
        stage: "smart_ops",
        auth_preset: "智慧运营 User-Info",
        method: "POST",
        url: "https://usm.ha.cmcc:19011/zhyypt/smop/export/exportData",
        headers: {
          Accept: "application/json, text/plain, */*",
          "Content-Type": "application/json",
          Origin: "https://usm.ha.cmcc:19011",
          Referer: "https://usm.ha.cmcc:19011/zhyypt/dist/",
        },
        body_type: "json",
        response_mode: "file",
        headers_from_session_storage: {
          "User-Info": "zhyyptInfo.accessToken",
        },
        data: {
          areaId: "AQ",
          dataLevel: "4",
          dataType: "dtal",
          queryDate: "${yesterday_yyyymmdd}",
          expTypeId: "HOLE_REAL_TC_EXP",
          expFileName: "运营总览-业务销量.xlsx",
          rankDown: null,
        },
      },
    ],
    compare_sources: [
      {
        download_name: "实时",
        engine: "openpyxl",
        max_workers: 4,
        sheet_mappings: [
          { name: "业务销量", new_sheet_name: "业务销量", template_sheet_name: "实时业务销量", header_row: 1, ignore_columns: [], key_columns: [] },
        ],
      },
      {
        download_name: "日累计",
        engine: "openpyxl",
        max_workers: 4,
        sheet_mappings: [
          { name: "业务销量", new_sheet_name: "业务销量", template_sheet_name: "日累计业务销量", header_row: 1, ignore_columns: [], key_columns: [] },
        ],
      },
    ],
    send: {
      webhook_url: "",
      workbook_name: "爱家亲情网每日盯控",
      items: [
        { type: "image", sheet: "通报" },
        { type: "text", sheet: "通报文字", text: { mode: "used_range" } },
      ],
    },
    template_update: {
      engine: "hybrid",
      update_condition: "any_changed",
      write_sheets: "all_compared",
      send_when_same: true,
    },
    wait_for_change: {
      enabled: false,
      poll_interval_seconds: 300,
      max_wait_minutes: 180,
    },
    deployment: {
      enabled: true,
      crons: ["0 9-18 * * *"],
      timezone: "Asia/Shanghai",
    },
  },
  {
    id: "city-ops-store-daily",
    name: "爱家亲情网营业厅每日盯控",
    template_path: "templates/爱家亲情网营业厅每日盯控表.xlsx",
    enabled: true,
    description: "地市作战平台下钻 JSON 转 Excel 后，进入相同通报流程。",
    lastRun: "running",
    updatedAt: "2026-05-12 22:54:18",
    downloads: [
      {
        name: "实时",
        stage: "city_ops",
        auth_preset: "地市作战 uapToken",
        method: "POST",
        url: "https://usm.ha.cmcc:19011/dszzCombat/dszzRestful/combatreal/getDetailByAreaAndIndex",
        headers: {
          Accept: "*/*",
          "Content-Type": "application/json",
          Origin: "https://usm.ha.cmcc:19011",
          Referer: "https://usm.ha.cmcc:19011/dszzCombat/dszzWeb/h5combatplatform/",
        },
        body_type: "json",
        response_mode: "json_drilldown_to_excel",
        headers_from_session_storage: { uapToken: "uapToken" },
        data: {
          indCodes: "sgs_ajvwdz,",
          areaId: "AQ",
          diyCodes: "sgs_ajvwdz",
          areaType: null,
          queryDate: "${today_yyyymmdd}",
        },
        excel: {
          data_path: "result.tableData",
          sheet_name: "地市作战实时明细",
          columns: [
            { field: "__level_name", header: "层级" },
            { field: "__parent_area_id", header: "父级areaId" },
            { field: "__request_area_id", header: "请求areaId" },
            { field: "areaName", header: "名称" },
            { field: "areaCode", header: "编码" },
            { field: "sgs_ajvwdz", header: "爱家亲情网(V网版)" },
          ],
        },
        drilldown: {
          data_path: "result.tableData",
          request_area_field: "areaId",
          next_area_field: "areaCode",
          levels: ["网格", "渠道经理", "渠道", "人员"],
          skip_self_row: true,
          max_requests: 1000,
        },
      },
    ],
    compare_sources: [
      {
        download_name: "实时",
        engine: "openpyxl",
        max_workers: 4,
        sheet_mappings: [
          { name: "实时", new_sheet_name: "地市作战实时明细", template_sheet_name: "营业厅实时销量", header_row: 1, ignore_columns: [], key_columns: [] },
        ],
      },
    ],
    send: {
      webhook_url: "",
      workbook_name: "爱家亲情网营业厅每日盯控",
      items: [
        { type: "image", sheet: "营业厅通报" },
        { type: "text", sheet: "营业厅通报文字", text: { mode: "used_range" } },
      ],
    },
    template_update: { engine: "hybrid", update_condition: "any_changed", write_sheets: "all_compared", send_when_same: true },
    wait_for_change: { enabled: false, poll_interval_seconds: 300, max_wait_minutes: 180 },
    deployment: { enabled: false, crons: [], timezone: "Asia/Shanghai" },
  },
];

export const initialLogs: RunLog[] = [
  { id: "log-1", status: "success", title: "最近一次发布", message: "deployment 已更新，等待 worker 拉取。", createdAt: "2026-05-12 18:10:23" },
  { id: "log-2", status: "running", title: "测试运行", message: "正在模拟触发 Prefect flow run。", createdAt: "2026-05-12 17:55:01" },
];
