import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { Button, MetaText, toast } from './ui'

type Job = { id: number; book_id: number; status: string; model: string; total: number; updated: number; skipped: number; failed: number; items: { chapter_id: number; title: string; status: string; message: string }[] }
const labels: Record<string, string> = { queued: '排队中', running: '生成中', cancelling: '正在停止', cancelled: '已停止', interrupted: '服务重启，等待继续', completed: '已完成', failed: '部分失败', pending: '待处理', skipped: '已跳过' }
const active = (job: Job | null) => !!job && ['queued', 'running', 'cancelling'].includes(job.status)

export function SummaryTaskPanel({ bookId, disabled, onBusy, onProgress }: { bookId: number; disabled: boolean; onBusy: (busy: boolean) => void; onProgress: () => void }) {
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const callbacks = useRef({ onBusy, onProgress })
  callbacks.current = { onBusy, onProgress }
  const signature = useRef('')
  const sequence = useRef(0)
  const accept = (next: Job | null) => {
    setJob(next); callbacks.current.onBusy(active(next))
    const key = next ? `${next.id}:${next.updated}:${next.skipped}:${next.failed}:${next.status}` : ''
    if (signature.current !== key) { signature.current = key; callbacks.current.onProgress() }
  }
  useEffect(() => {
    let stopped = false
    let timer: number
    const poll = async () => {
      const token = sequence.current
      try {
        const next = await api<Job | null>(`/books/${bookId}/summary-jobs/latest`)
        if (!stopped && token === sequence.current) { accept(next); setError('') }
      } catch { if (!stopped) setError('任务状态读取失败，正在重试。') }
      if (!stopped) timer = window.setTimeout(poll, 1500)
    }
    void poll()
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [bookId])
  const act = async (path: string) => {
    sequence.current++; setSending(true); setError('')
    try { accept(await api<Job>(path, { method: 'POST' })) }
    catch (exc) { setError(String(exc)); toast('任务操作失败', 'danger') }
    finally { sequence.current++; setSending(false) }
  }
  return <div className="space-y-2">
    <Button variant="ghost" size="sm" className="w-full" disabled={disabled || sending || active(job)} onClick={() => void act(`/books/${bookId}/summary-jobs`)}>批量更新章节梗概</Button>
    {job && <div className="rounded border border-line-soft p-2 text-meta" aria-label="批量梗概任务">
      <div>{labels[job.status] || job.status} · 主模型：{job.model}</div>
      <progress className="mt-1 w-full accent-accent-600" max={Math.max(job.total, 1)} value={job.updated + job.skipped + job.failed} aria-label="梗概任务进度" />
      <MetaText>梗概更新：成功 {job.updated} 章，跳过 {job.skipped} 章，失败 {job.failed} 章</MetaText>
      {active(job)
        ? <Button size="sm" variant="ghost" disabled={sending || job.status === 'cancelling'} onClick={() => void act(`/summary-jobs/${job.id}/cancel`)}>停止任务</Button>
        : ['cancelled', 'interrupted', 'failed'].includes(job.status) && <Button size="sm" variant="secondary" disabled={disabled || sending} onClick={() => void act(`/summary-jobs/${job.id}/resume`)}>继续／重试失败章节</Button>}
      {job.status === 'cancelling' && <MetaText>当前章结束后停止，已完成结果保留。</MetaText>}
      <details className="mt-1"><summary className="cursor-pointer">查看章节进度 · {job.total} 章</summary>
        <div className="max-h-48 overflow-y-auto">{job.items.map(item => <div className="mt-2" key={item.chapter_id}>{item.title} · {labels[item.status] || item.status}<div className="text-ink-faint">{item.message}</div></div>)}</div>
      </details>
    </div>}
    {error && <div role="alert" className="text-meta text-danger">{error}</div>}
  </div>
}
