import { useEffect, useRef, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { api } from '../api/client'
import { Button, MetaText, Select } from './ui'

type Job = { id: number; status: string; step: string; message: string; target_count: number; completed_count: number; saved_count: number; model: string;
  items: { sequence: number; chapter_id: number | null; title: string; status: string }[] }
const active = (job: Job | null) => !!job && ['queued', 'running', 'cancelling'].includes(job.status)
const labels: Record<string, string> = { queued: '排队中', running: '生成中', cancelling: '正在停止', cancelled: '已停止', interrupted: '等待继续', failed: '需处理', completed: '已完成' }
const steps: Record<string, string> = { preparing: '核对资料', waiting: '等待其他任务', summaries: '补齐梗概', state: '更新速查', writing: '生成正文', completed: '已完成' }

export function WritingTaskControl({ bookId, disabled, onBusy, onProgress, onOpen }: {
  bookId: number; disabled: boolean; onBusy: (busy: boolean) => void; onProgress: () => void; onOpen: (id: number) => void
}) {
  const [count, setCount] = useState('1')
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const [loading, setLoading] = useState(true)
  const callbacks = useRef({ onBusy, onProgress, onOpen })
  callbacks.current = { onBusy, onProgress, onOpen }
  const signature = useRef('')
  const sequence = useRef(0)
  const alive = useRef(false)
  const accept = (next: Job | null) => {
    setJob(next); callbacks.current.onBusy(active(next))
    const key = next ? `${next.id}:${next.saved_count}:${next.completed_count}:${next.status}` : ''
    if (key !== signature.current) { signature.current = key; callbacks.current.onProgress() }
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
    return () => { stopped = true; alive.current = false; window.clearTimeout(timer); callbacks.current.onBusy(false) }
  }, [bookId])
  const act = async (path: string, start = false) => {
    sequence.current++; setSending(true); setError('')
    try {
      const next = await api<Job>(path, { method: 'POST', ...(start ? { body: JSON.stringify({ chapter_count: Number(count) }) } : {}) })
      if (alive.current) accept(next)
    } catch (exc) { if (alive.current) setError(String(exc)) }
    finally { sequence.current++; if (alive.current) setSending(false) }
  }
  return <div className="max-w-full space-y-1" aria-label="持续生成正文">
    <div className="flex flex-wrap items-center gap-1.5">
      <Select aria-label="持续生成章数" value={count} disabled={disabled || loading || sending || active(job)} onChange={e => setCount(e.target.value)} className="!w-24 py-1.5 text-meta">
        {[1, 3, 5, 10, 20, 30, 50, 100].map(n => <option key={n} value={n}>{n} 章</option>)}
      </Select>
      <Button size="sm" variant="primary" disabled={disabled || loading || sending || active(job)} title={disabled ? '请先保存正文修改，或等待当前操作结束' : '补齐梗概和速查后自动生成并保存指定章数'} onClick={() => void act(`/books/${bookId}/writing-jobs`, true)}><Sparkles size={14} />开始生成正文</Button>
      {active(job) && <Button size="sm" variant="secondary" disabled={sending || job?.status === 'cancelling'} onClick={() => void act(`/writing-jobs/${job?.id}/cancel`)}>停止生成</Button>}
      {job && ['failed', 'cancelled', 'interrupted'].includes(job.status) && <Button size="sm" variant="secondary" disabled={disabled || sending} onClick={() => void act(`/writing-jobs/${job.id}/resume`)}>继续生成</Button>}
    </div>
    {job && <details className="max-w-lg text-meta" aria-label="正文生成进度">
      <summary className="cursor-pointer text-ink-soft">{labels[job.status]} · {steps[job.step] || job.step} · 正文已保存 {job.saved_count}/{job.target_count} 章 · 归档完成 {job.completed_count} 章</summary>
      <div className="space-y-1 rounded border border-line-soft bg-paper p-2">
        <MetaText>{job.model} · {job.message}</MetaText>
        {job.status === 'cancelling' && <MetaText>当前步骤完成后停止，已保存正文和进度保留。</MetaText>}
        <progress className="w-full accent-accent-600" max={job.target_count} value={job.completed_count} aria-label="正文任务进度" />
        <div className="max-h-40 overflow-y-auto">{job.items.filter(i => i.status !== 'pending').map(i => <div key={i.sequence} className="flex items-center gap-2">
          <span>{i.sequence}. {i.title} · {i.status === 'completed' ? '已归档' : '正文已保存'}</span>
          {i.chapter_id && <Button size="sm" variant="ghost" onClick={() => callbacks.current.onOpen(i.chapter_id!)}>查看章节</Button>}
        </div>)}</div>
      </div>
    </details>}
    {(error || job?.status === 'failed') && <div role="alert" className="max-w-lg text-meta text-danger">{error || job?.message}</div>}
  </div>
}
