import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { Badge, Button, InlineError, MetaText, Panel, toast } from './ui'
import { TextContent } from './TextContent'
import type { Chapter } from '../types'

export type PlanVersion = { id: number; plan_id: string; version_number: number; title: string; volume: string; content: string; status: 'draft' | 'confirmed'; note: string; created_at: string }
export type ChapterPlan = { id: string; book_id: number; position: number; version: PlanVersion; chapter_id: number | null; chapter_title: string | null; chapter_number: number | null; chapter_version_id: number | null; needs_review: boolean; uses: { plan_version_id: number; chapter_version_id: number; chapter_id: number; body_version_number: number }[] }
export type PlanList = { plans: ChapterPlan[]; guidance: { id: number; content: string; material_key: string } | null }

export function ChapterPlanPanel({ bookId, chapterId, versionId, chapter, disabled, onConfirmed }: { bookId: number; chapterId: number; versionId: number | null; chapter?: Chapter; disabled?: boolean; onConfirmed?: (chapter: Chapter) => void }) {
  const [data, setData] = useState<PlanList | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let alive = true
    const load = () => { void api<PlanList>(`/books/${bookId}/chapter-plans`).then(d => { if (alive) { setData(d); setError('') } }).catch(e => { if (alive) setError(String(e)) }) }
    load(); window.addEventListener('focus', load)
    return () => { alive = false; window.removeEventListener('focus', load) }
  }, [bookId, chapterId, versionId])
  const plan = data?.plans.find(p => p.chapter_id === chapterId)
  const confirmed = chapter?.status === 'approved' && !plan?.needs_review
  const confirm = async () => {
    if (!data || !chapter || !versionId || busy) return
    setBusy(true); setError('')
    try {
      const saved = await api<Chapter>(`/chapters/${chapterId}/confirm`, {method: 'POST', body: JSON.stringify({base_version_id: versionId, plan_version_id: plan?.version.id ?? null})})
      onConfirmed?.(saved)
      setData(d => d && ({...d, plans: d.plans.map(p => p.id === plan?.id ? {...p, needs_review: false} : p)}))
      toast('正文已确认为正式稿，当前章纲核对状态已同步')
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  return <Panel title="本章章纲">
    {chapter && <div className="mb-3 flex items-center justify-between gap-2"><Badge tone={confirmed ? 'ok' : 'warn'}>{confirmed ? '正式稿 · 已确认' : chapter.word_count ? '草稿 · 待确认' : '空白正文'}</Badge><Button size="sm" variant="primary" disabled={disabled || busy || !data || !chapter.word_count || confirmed} onClick={() => void confirm()}>{busy ? '确认中…' : confirmed ? '已确认正文' : '确认正文'}</Button></div>}
    {error ? <InlineError message={error} /> : !data ? <MetaText>读取中…</MetaText> : plan ? <>
      <div className="flex flex-wrap gap-2"><Badge>{plan.version.status === 'confirmed' ? '章纲已确认' : '章纲草稿'}</Badge>{plan.needs_review && <MetaText>核对本章后点击“确认正文”</MetaText>}</div>
      <MetaText className="mt-2">{plan.version.volume} · {plan.version.title} · v{plan.version.version_number}</MetaText>
      <details className="mt-3 text-body"><summary className="cursor-pointer text-accent-700">查看章纲</summary><TextContent label="本章章纲内容" className="mt-3 leading-7" text={plan.version.content} /></details>
      <Link className="mt-3 block text-meta text-accent-700" to={`/books/${bookId}/chapter-plans/${plan.id}`}>管理章纲与关联</Link>
    </> : <><MetaText>尚未关联章纲。</MetaText><Link className="mt-2 block text-meta text-accent-700" to={`/books/${bookId}/chapter-plans`}>前往章纲页面关联已有正文</Link></>}
  </Panel>
}
