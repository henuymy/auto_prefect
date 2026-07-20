import { CalendarClock, Clock3, Info, X } from "lucide-react";
import type { PendingQueue } from "./types";
import { formatMonitorDateTime } from "./time";
import { useDrawerDialog } from "./useDrawerDialog";

export function PendingQueueDrawer({ queue, onClose }: { queue: PendingQueue; onClose: () => void }) {
  const { dialogRef, closeButtonRef } = useDrawerDialog(onClose);
  return <aside ref={dialogRef} className="monitor-detail pending-drawer" role="dialog" aria-modal="true" aria-label="待执行队列">
    <div className="detail-heading">
      <div><p className="detail-overline">计划队列</p><h2>待执行队列</h2><div className="detail-meta"><span>{queue.scopeLabel}</span><span className="status-dot status-scheduled">{queue.total} 个计划实例</span></div></div>
      <button ref={closeButtonRef} className="icon-button" aria-label="关闭待执行队列" onClick={onClose}><X size={18} /></button>
    </div>
    <div className="queue-summary"><CalendarClock size={19} /><div><strong>{queue.total}</strong><span>个计划实例处于 Scheduled 状态</span></div></div>
    <p className="queue-note"><Info size={14} />全局通报 Scheduled 队列，不受运行记录筛选影响。按计划时间排序；每一条均是数据库中的独立 Scheduled 计划实例，不按运行对象合并。</p>
    <section className="detail-section" aria-label="按计划时间排序的待执行计划"><div className="section-row"><h3>计划时间线</h3><span className="queue-caption">共 {queue.total} 条</span></div><div className="queue-list">
      {queue.items.map((item) => <div className="queue-row" key={item.id}><div><strong>{item.target}</strong><p>{item.trigger === "session" ? "会话维护" : item.trigger === "web" ? "网页操作" : "定时调度"} · 下一环节：{item.nextStep}</p></div><time><Clock3 size={13} />{formatMonitorDateTime(item.scheduledAt)}</time></div>)}
    </div></section>
  </aside>;
}
