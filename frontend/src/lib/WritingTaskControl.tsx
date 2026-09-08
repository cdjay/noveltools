import { useEffect, useRef, useState } from 'react'
import { GripHorizontal, Minus, Plus, Sparkles, X } from 'lucide-react'
import { createPortal } from 'react-dom'
import { create } from 'zustand'
import { useNavigate } from 'react-router-dom'
import { useAppStore } from '../stores/useAppStore'
import type { Chapter } from '../types'
import { api } from '../api/client'
import { Button, MetaText, Select, toast } from './ui'

type Job = { id: number; status: string; step: string; message: string; target_count: number; completed_count: number; saved_count: number; model: string;
  failover_events?: { id: number; time: string; text: string }[];
  items: { sequence: number; chapter_id: number | null; title: string; status: string }[] }
const active = (job: Job | null) => !!job && ['queued', 'running', 'cancelling'].includes(job.status)
const labels: Record<string, string> = { queued: '排队中', running: '生成中', cancelling: '正在停止', cancelled: '已停止', interrupted: '等待继续', failed: '需处理', completed: '已完成' }
const steps: Record<string, string> = { preparing: '核对资料', waiting: '等待其他任务', summaries: '补齐梗概', state: '更新速查', writing: '生成正文', completed: '已完成' }

type Controls = { bookId: number; disabled: boolean; onBusy: (busy: boolean) => void; onOpen: (id: number) => void }
const useWritingWindow = create<{ bookId: number | null; bookTitle: string; open: boolean; busy: boolean; controls: Controls | null }>(() => ({ bookId: null, bookTitle: '', open: false, busy: false, controls: null }))

type LogEntry = { key: string; time: string; text: string }
type TaskLog = { jobId: number | null; entries: LogEntry[] }
function readLog(bookId: number): TaskLog {
  try {
    const saved = JSON.parse(sessionStorage.getItem(`writing-log:${bookId}`) || 'null')
    if (saved && typeof saved.jobId === 'number' && Array.isArray(saved.entries)) return {
      jobId: saved.jobId, entries: saved.entries.filter((entry: LogEntry) =>
        typeof entry.key === 'string' && typeof entry.time === 'string' && typeof entry.text === 'string').slice(-100),
    }
  } catch { /* Storage can be unavailable in private contexts. */ }
  return { jobId: null, entries: [] }
}

function TaskProgress({ job }: { job: Job }) {
  return <div role="progressbar" aria-label="正文任务进度" aria-valuemin={0} aria-valuemax={job.target_count} aria-valuenow={job.completed_count}
    aria-valuetext={`正文已保存 ${job.saved_count}/${job.target_count} 章，归档完成 ${job.completed_count} 章`}
    className="h-1.5 overflow-hidden rounded-full bg-paper-sunken">
    <div className="h-full rounded-full bg-accent-600 transition-all" style={{ width: `${Math.min(100, 100 * job.completed_count / Math.max(1, job.target_count))}%` }} />
  </div>
}

export function WritingTaskControl(props: Controls) {
  const busy = useWritingWindow(s => s.bookId === props.bookId && s.busy)
  const callbacks = useRef(props)
  callbacks.current = props
  useEffect(() => {
    const window = useWritingWindow.getState()
    const controls = { bookId: props.bookId, disabled: props.disabled,
      onBusy: (busy: boolean) => callbacks.current.onBusy(busy),
      onOpen: (id: number) => callbacks.current.onOpen(id) }
    useWritingWindow.setState({ controls, ...(!window.open && window.bookId !== props.bookId ? {
      bookId: props.bookId, bookTitle: useAppStore.getState().currentBook?.title || '', busy: false,
    } : {}) })
    return () => { if (useWritingWindow.getState().controls === controls) useWritingWindow.setState({ controls: null }) }
  }, [props.bookId, props.disabled])
  useEffect(() => { props.onBusy(busy) }, [busy, props.onBusy])
  return <Button size="sm" variant={busy ? 'primary' : 'secondary'} onClick={() => {
    const current = useWritingWindow.getState()
    useWritingWindow.setState({ bookId: props.bookId, bookTitle: useAppStore.getState().currentBook?.title || '', open: true,
      ...(current.bookId !== props.bookId ? { busy: false } : {}) })
  }}><Sparkles size={14} />持续生成正文</Button>
}

export function WritingTaskHost() {
  const bookId = useWritingWindow(s => s.bookId)
  return bookId === null ? null : <WritingTaskWindow key={bookId} bookId={bookId} />
}

function WritingTaskWindow({ bookId }: { bookId: number }) {
  const { open, bookTitle, controls } = useWritingWindow()
  const disabled = controls?.bookId === bookId && controls.disabled
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(false)
  const [position, setPosition] = useState<{ x: number; y: number } | null>(null)
  const panel = useRef<HTMLElement>(null)
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null)
  const refresh = useRef(0)
  const setOpen = (open: boolean) => useWritingWindow.setState({ open })
  const [count, setCount] = useState('1')
  const [job, setJob] = useState<Job | null>(null)
  const [log, setLog] = useState<TaskLog>(() => readLog(bookId))
  const logRef = useRef(log)
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const [loading, setLoading] = useState(true)
  const signature = useRef('')
  const sequence = useRef(0)
  const alive = useRef(false)
  const previousJob = useRef<Job | null>(null)
  const accept = (next: Job | null) => {
    if (next && previousJob.current?.id === next.id && active(previousJob.current)) {
      if (next.status === 'failed') toast(`《${bookTitle}》持续生成已停止：${next.message}`, 'danger')
      if (next.status === 'completed') toast(`《${bookTitle}》已完成 ${next.completed_count} 章正文、梗概与速查。`)
    }
    previousJob.current = next
    setJob(next); useWritingWindow.setState({ busy: active(next) })
    if (next) {
      const key = JSON.stringify([next.status, next.step, next.message, next.saved_count, next.completed_count])
      const entries = logRef.current.jobId === next.id ? logRef.current.entries : []
      if (entries[entries.length - 1]?.key !== key) {
        const updated = { jobId: next.id, entries: [...entries, { key, time: new Date().toISOString(),
          text: `${labels[next.status]} · ${steps[next.step] || next.step}：${next.message || '等待处理'}（正文 ${next.saved_count}/${next.target_count}，归档 ${next.completed_count}）` }].slice(-100) }
        logRef.current = updated; setLog(updated)
        try { sessionStorage.setItem(`writing-log:${bookId}`, JSON.stringify(updated)) } catch { /* Progress remains available without storage. */ }
      }
    }
    const key = next ? JSON.stringify([next.id, next.saved_count, next.completed_count, next.status, next.step, next.message]) : ''
    if (key !== signature.current) {
      signature.current = key
      const token = ++refresh.current
      void api<Chapter[]>(`/books/${bookId}/chapters?include_content=false`).then(chapters => {
        const state = useAppStore.getState()
        if (!alive.current || token !== refresh.current || state.currentBook?.id !== bookId) return
        state.setChapters(chapters)
        const selected = state.currentChapter
        const fresh = chapters.find(chapter => chapter.id === selected?.id)
        // Merge only summary metadata: a list response deliberately omits body text.
        if (selected && fresh) state.setCurrentChapter({ ...selected, summary: fresh.summary,
          summary_content_sha256: fresh.summary_content_sha256, summary_source_version_id: fresh.summary_source_version_id,
          summary_model: fresh.summary_model, summary_updated_at: fresh.summary_updated_at,
          summary_is_stale: fresh.summary_is_stale || fresh.current_version_id !== selected.current_version_id })
      }).catch(() => { if (alive.current && token === refresh.current) signature.current = '' })
    }
  }
  useEffect(() => {
    alive.current = true
    let stopped = false
    let timer: number
    const poll = async () => {
      const token = sequence.current
      try {
        const next = await api<Job | null>(`/books/${bookId}/writing-jobs/latest`)
        if (!stopped && token === sequence.current) {
          accept(next); setLoading(false)
          setError(previous => previous === '正文任务状态读取失败，正在重试。' ? '' : previous)
        }
      } catch { if (!stopped) setError('正文任务状态读取失败，正在重试。') }
      if (!stopped) timer = window.setTimeout(poll, 1500)
    }
    void poll()
    return () => { stopped = true; alive.current = false; window.clearTimeout(timer) }
  }, [bookId])
  // Keep a dragged or expanded window within reach, including after viewport changes.
  useEffect(() => {
    if (!open || !panel.current) return
    const clamp = () => {
      const rect = panel.current!.getBoundingClientRect()
      setPosition(previous => {
        if (!previous) return null
        const x = Math.max(16, Math.min(previous.x, window.innerWidth - rect.width - 16))
        const y = Math.max(16, Math.min(previous.y, window.innerHeight - rect.height - 16))
        return previous?.x === x && previous?.y === y ? previous : { x, y }
      })
    }
    const observer = new ResizeObserver(clamp)
    observer.observe(panel.current)
    window.addEventListener('resize', clamp)
    clamp()
    return () => { observer.disconnect(); window.removeEventListener('resize', clamp) }
  }, [open])
  const act = async (path: string, start = false) => {
    sequence.current++; setSending(true); setError('')
    try {
      const next = await api<Job>(path, { method: 'POST', ...(start ? { body: JSON.stringify({ chapter_count: Number(count) }) } : {}) })
      if (alive.current) accept(next)
    } catch (exc) { if (alive.current) setError(String(exc)) }
    finally { sequence.current++; if (alive.current) setSending(false) }
  }
  const visibleLog = [...log.entries, ...(job?.failover_events || []).map(event => ({ ...event, key: `failover:${event.id}` }))]
    .sort((a, b) => b.time.localeCompare(a.time)).slice(0, 100)
  if (!open) return null
  return createPortal(<section ref={panel} role="region" aria-label="持续生成正文浮窗"
    style={position ? { left: position.x, top: position.y } : { right: 20, bottom: 20 }}
    className="fixed z-40 flex max-h-[calc(100vh-32px)] w-[420px] max-w-[calc(100vw-32px)] flex-col overflow-hidden rounded-lg border border-line-strong bg-paper-raised text-ink shadow-pop">
    <header className="flex shrink-0 touch-none items-center gap-2 border-b border-line-soft px-4 py-3" style={{ cursor: 'move' }}
      onPointerDown={e => {
        if ((e.target as HTMLElement).closest('button')) return
        const rect = panel.current!.getBoundingClientRect()
        drag.current = { x: e.clientX, y: e.clientY, left: rect.left, top: rect.top }
        e.currentTarget.setPointerCapture(e.pointerId)
      }}
      onPointerMove={e => {
        if (!drag.current) return
        const rect = panel.current!.getBoundingClientRect()
        setPosition({ x: Math.max(8, Math.min(window.innerWidth - rect.width - 8, drag.current.left + e.clientX - drag.current.x)),
          y: Math.max(8, Math.min(window.innerHeight - rect.height - 8, drag.current.top + e.clientY - drag.current.y)) })
      }} onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }}>
      <GripHorizontal size={15} className="shrink-0 text-ink-faint" />
      <div className="min-w-0 flex-1"><div className="text-body font-semibold">持续生成正文</div><MetaText className="truncate">{bookTitle}{job ? ` · ${labels[job.status]} · ${job.saved_count}/${job.target_count} 章` : ''}</MetaText></div>
      <Button size="sm" variant="ghost" aria-label={collapsed ? '展开生成浮窗' : '收起生成浮窗'} onClick={() => setCollapsed(!collapsed)}>{collapsed ? <Plus size={14} /> : <Minus size={14} />}</Button>
      <Button size="sm" variant="ghost" aria-label="关闭生成浮窗" onClick={() => setOpen(false)}><X size={14} /></Button>
    </header>
    {collapsed && <div className="space-y-2 px-4 py-3">
      <div className={`truncate text-meta ${job?.status === 'failed' || error ? 'text-danger' : 'text-ink-soft'}`}>{error || (job ? `${steps[job.step] || job.step} · ${job.message}` : '尚未开始')}</div>
      {job && <TaskProgress job={job} />}
    </div>}
    <div hidden={collapsed} className="min-h-0 space-y-3 overflow-y-auto p-4">
    <MetaText>拖动标题栏可移动。关闭或切换页面不停止任务。</MetaText>
    <div className="flex flex-wrap items-center gap-1.5">
      <Select aria-label="持续生成章数" value={count} disabled={disabled || loading || sending || active(job)} onChange={e => setCount(e.target.value)} className="!w-24 py-1.5 text-meta">
        {[1, 3, 5, 10, 20, 30, 50, 100].map(n => <option key={n} value={n}>{n} 章</option>)}
      </Select>
      <Button size="sm" variant="primary" disabled={disabled || loading || sending || active(job)} title={disabled ? '请先保存正文修改，或等待当前操作结束' : '补齐梗概和速查后自动生成并保存指定章数'} onClick={() => void act(`/books/${bookId}/writing-jobs`, true)}><Sparkles size={14} />开始生成正文</Button>
      {active(job) && <Button size="sm" variant="secondary" disabled={sending || job?.status === 'cancelling'} onClick={() => void act(`/writing-jobs/${job?.id}/cancel`)}>停止生成</Button>}
      {job && ['failed', 'cancelled', 'interrupted'].includes(job.status) && <Button size="sm" variant="secondary" disabled={disabled || sending} onClick={() => void act(`/writing-jobs/${job.id}/resume`)}>继续生成</Button>}
    </div>
    {job && <section className="mt-4 space-y-3 rounded-md border border-line-soft bg-paper p-4 text-meta" aria-label="正文生成进度">
      <div className="font-medium text-ink">{labels[job.status]} · {steps[job.step] || job.step}</div>
      <div className="text-ink-soft">正文已保存 {job.saved_count}/{job.target_count} 章 · 归档完成 {job.completed_count} 章</div>
      <div className="space-y-2">
        <MetaText>当前步骤：{job.message}</MetaText>
        <MetaText>正文模型：{job.model}</MetaText>
        {job.status === 'cancelling' && <MetaText>当前步骤完成后停止，已保存正文和进度保留。</MetaText>}
        <TaskProgress job={job} />
        <div className="max-h-40 overflow-y-auto">{job.items.filter(i => i.status !== 'pending').map(i => <div key={i.sequence} className="flex items-center gap-2">
          <span>{i.sequence}. {i.title} · {i.status === 'completed' ? '已归档' : '正文已保存'}</span>
          {i.chapter_id && <Button size="sm" variant="ghost" onClick={() => {
            const current = useWritingWindow.getState().controls
            if (current?.bookId === bookId) current.onOpen(i.chapter_id!)
            else navigate(`/books/${bookId}/reader`)
          }}>{controls?.bookId === bookId ? '查看章节' : '返回正文'}</Button>}
        </div>)}</div>
      </div>
    </section>}
    {job && log.jobId === job.id && <section aria-label="步骤日志" className="border-t border-line-soft pt-3">
      <div className="mb-2 flex items-center justify-between"><h3 className="text-meta font-semibold">步骤日志</h3><MetaText>步骤与模型切换 · 最近 100 条</MetaText></div>
      <ol className="max-h-36 space-y-2 overflow-y-auto text-meta" aria-label="任务日志记录">
        {visibleLog.map((entry, index) => <li key={`${entry.time}:${index}`} className="flex gap-2">
          <time className="shrink-0 tabular-nums text-ink-faint" dateTime={entry.time}>{new Date(entry.time).toLocaleTimeString('zh-CN', { hour12: false })}</time>
          <span className="break-words text-ink-soft">{entry.text}</span>
        </li>)}
      </ol>
    </section>}
    {(error || job?.status === 'failed') && <div role="alert" className="mt-3 text-meta text-danger">{error || job?.message}</div>}
    </div>
  </section>, document.body)
}
