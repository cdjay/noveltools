import { useEffect, useMemo, useRef, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { api } from '../api/client'
import { Button, Field, InlineError, Input, MetaText, Modal, Select, toast } from './ui'

type PlanOption = { id: string; position: number; title: string; confirmed: boolean; has_body: boolean; chapter_id: number | null }
type Options = { start_chapter: number; plans: PlanOption[] }
type JobItem = { chapter_id: number | null; position?: number; status: string }
type Job = { id: number; status: string; message: string; target_count: number; saved_count: number; completed_count: number; items: JobItem[] }

const active = (job: Job | null) => !!job && ['queued', 'running', 'cancelling'].includes(job.status)
// A parent task can stop polling because it is waiting for an author decision.
// Keep that task visible after a refresh so the chapter entry does not look
// stuck and accidentally start a second task for the same book.
const needsAttention = (job: Job | null) => !!job && ['paused', 'failed', 'interrupted'].includes(job.status)

type Props = {
  bookId: number
  startChapter: number
  startTitle?: string
  hasBody?: boolean
  disabled?: boolean
  label?: string
  onBusy?: (busy: boolean) => void
  onOpenChapter?: (chapterId: number) => void
}

/** Shared chapter-plan and reader entry point for the persisted canvas pipeline. */
export function ChapterGenerationButton({ bookId, startChapter, startTitle, hasBody = false, disabled = false,
  label = '工作流生成正文', onBusy, onOpenChapter }: Props) {
  const [open, setOpen] = useState(false)
  const [options, setOptions] = useState<Options | null>(null)
  const [count, setCount] = useState('1')
  const [reviewLimit, setReviewLimit] = useState('3')
  const [autoRevise, setAutoRevise] = useState(true)
  const [replaceExisting, setReplaceExisting] = useState(hasBody)
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [sending, setSending] = useState(false)
  const startedRef = useRef(false)
  const entry = startTitle ? 'chapter_plan' : 'reader'

  useEffect(() => { onBusy?.(active(job) || sending); return () => onBusy?.(false) }, [job, sending, onBusy])
  useEffect(() => {
    if (!open) return
    let alive = true
    setLoading(true); setError(''); setJob(null); setReplaceExisting(hasBody)
    void Promise.all([
      api<Options>(`/books/${bookId}/writing-jobs/generation-options`),
      api<Job | null>(`/books/${bookId}/writing-jobs/latest`).catch(() => null),
    ]).then(([value, latest]) => {
      if (!alive) return
      setOptions(value)
      // The parent writing job owns the durable task. Restore it when the
      // page is refreshed so the user can background it or inspect progress
      // instead of accidentally submitting a duplicate request.
      if (latest && (active(latest) || needsAttention(latest))) setJob(latest)
    }).catch(exc => { if (alive) setError(String(exc)) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [open, bookId, startChapter, hasBody])
  useEffect(() => {
    if (!job || !active(job)) return
    let alive = true
    const timer = window.setTimeout(async function poll() {
      try {
        const next = await api<Job>(`/writing-jobs/${job.id}`)
        if (!alive) return
        setJob(next)
        if (active(next)) window.setTimeout(poll, 1500)
        else if (next.status === 'completed') {
          toast(`第${next.items.length ? (next.items[0].position || startChapter) : startChapter}—${startChapter + next.target_count - 1}章工作流已完成，请审阅正文`)
          const first = next.items.find(item => item.chapter_id)
          if (first?.chapter_id && onOpenChapter) onOpenChapter(first.chapter_id)
        } else if (next.status === 'paused' || next.status === 'failed') setError(next.message || '工作流需要处理')
      } catch (exc) { if (alive) setError(String(exc)) }
    }, 700)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [job?.id, job?.status, startChapter, onOpenChapter])

  const total = Math.max(1, Number(count) || 0)
  const end = startChapter + total - 1
  const selected = useMemo(() => options?.plans.filter(p => p.position >= startChapter && p.position <= end) || [], [options, startChapter, end])
  const issue = loading ? '正在读取章纲和正文关联…' : error && !options ? error : !options ? '正在读取可生成范围…'
    : !Number.isInteger(total) || total < 1 || total > 1000 ? '生成数量应为1—1000章。'
    : selected.length !== total ? `第${startChapter}—${end}章的章纲不齐全，请先补齐。`
    : selected.some(p => !p.confirmed) ? `第${selected.find(p => !p.confirmed)?.position}章章纲尚未确认，请先保存为“已确认”。`
    : selected.some(p => p.has_body) && !replaceExisting ? '范围内已有正文，请勾选“生成新版本并保留旧稿”。'
    : ''
  const begin = async () => {
    if (issue || sending || active(job) || needsAttention(job)) return
    setSending(true); setError(''); startedRef.current = true
    try {
      const next = await api<Job>(`/books/${bookId}/writing-jobs`, { method: 'POST', body: JSON.stringify({
        mode: 'count', chapter_count: total, start_chapter: startChapter, replace_existing: replaceExisting,
        entry, review_limit: Number(reviewLimit), auto_revise: autoRevise,
      }) })
      setJob(next); toast(`已安排第${startChapter}—${end}章，工作流将保存每章冻结资料`)
    } catch (exc) { setError(String(exc)) } finally { setSending(false) }
  }
  // Closing the modal only hides the view. The persisted parent/child job
  // continues in the background and can be restored after a refresh.
  const close = () => { if (!sending) setOpen(false) }
  return <>
    <Button size="sm" variant="primary" disabled={disabled || sending} onClick={() => setOpen(true)}><Sparkles size={14} />{label}</Button>
    <Modal open={open} onClose={close} title={startTitle ? `生成正文 · 第${startChapter}章《${startTitle}》` : `生成正文 · 第${startChapter}章`}
      description="正文页和章纲页使用同一条持久化工作流；每个章节会冻结本次输入资料，旧正文保留在历史版本中。" width="max-w-xl"
      footer={<><Button variant="ghost" disabled={sending} onClick={close}>{active(job) ? '后台运行' : '取消'}</Button><Button variant="primary" disabled={!!issue || sending || loading || active(job) || needsAttention(job)} onClick={() => void begin()}>{sending ? '提交中…' : active(job) ? '工作流运行中…' : needsAttention(job) ? '请先处理当前任务' : '开始生成'}</Button></>}>
      {error && <InlineError message={error} />}
      <div className="space-y-4">
        <Field label="生成章数" hint={`从第${startChapter}章开始，连续处理完整章纲；不可跳过缺章。`}><Input aria-label="生成章数" type="number" min={1} max={1000} value={count} disabled={sending || active(job)} onChange={e => setCount(e.target.value)} /></Field>
        <div className="rounded-md border border-line-soft bg-paper p-3 text-meta"><div className="font-medium text-ink">目标范围：第{startChapter}—{end}章，共{total}章</div>{selected.length > 0 && <div className="mt-1 space-y-1 text-ink-soft">{selected.map(p => <div key={p.id}>第{p.position}章 · {p.title} · {p.confirmed ? '已确认' : '未确认'}{p.has_body ? ' · 已有正文' : ' · 待生成'}</div>)}</div>}</div>
        <Field label="每章最多完整审核"><Select aria-label="每章最多完整审核" value={reviewLimit} disabled={sending || active(job)} onChange={e => setReviewLimit(e.target.value)}>{[0,1,2,3,4,5].map(n => <option key={n} value={n}>{n === 0 ? '不审核，仅生成' : `${n}次`}</option>)}</Select></Field>
        {reviewLimit !== '0' && <label className="flex items-start gap-2 text-meta"><input type="checkbox" className="mt-1" checked={autoRevise} disabled={sending || active(job)} onChange={e => setAutoRevise(e.target.checked)} /><span>允许工作流自动修订有证据的确定问题；需要作者裁定时暂停。</span></label>}
        {selected.some(p => p.has_body) && <label className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-meta text-warn-strong"><input type="checkbox" className="mt-1" checked={replaceExisting} disabled={sending || active(job)} onChange={e => setReplaceExisting(e.target.checked)} /><span>生成新版本并保留旧稿（会重新读取当前正文作为基准，原版本不会删除）。</span></label>}
        <p className={`text-meta ${issue ? 'text-warn-strong' : 'text-accent-700'}`} aria-live="polite">{issue || '资料准备完成后才会启动画布；不会把未来章节写入前文状态。'}</p>
        {job && <section className="rounded-md border border-line-soft bg-paper p-3 text-meta" aria-label="本次正文工作流进度"><div className="font-medium text-ink">{job.status === 'completed' ? '已完成' : job.status === 'paused' ? '待人工处理' : job.status === 'failed' ? '失败' : '运行中'}</div><div className="mt-1 text-ink-soft">正文已保存 {job.saved_count}/{job.target_count} 章 · 流程完成 {job.completed_count} 章</div><div className="mt-1 text-ink-faint">{job.message}</div>{needsAttention(job) && <div className="mt-2 text-warn-strong">请打开“持续生成正文”查看审核意见；处理并确认后，再点击“处理后继续”。</div>}</section>}
        {startedRef.current && <MetaText>关闭弹窗不会停止任务；可在正文页或章纲页的“持续生成正文”中查看任务进度。</MetaText>}
      </div>
    </Modal>
  </>
}
