import type { DownloadItem } from "@/types/config";

const smartbookDownload: DownloadItem = {
  source: "tencent_smartbook",
  name: "智能表格日报",
  doc_url: "https://docs.qq.com/smartsheet/Dexample?tab=sheet-1",
  output_filename: "智能表格日报.xlsx",
  headers: {},
  sheets: [{ sheet_id: "sheet-1", sheet_name: "汇总", output_sheet_name: "汇总" }],
};

void smartbookDownload;
