import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { Badge, InlineError, MetaText, Panel } from './ui'
import { TextContent } from './TextContent'

export type PlanVersion = { id: number; plan_id: string; version_number: number; title: string; volume: string; content: string; status: 'draft' | 'confirmed'; note: string; created_at: string }
export type ChapterPlan = { id: string; book_id: number; position: number; version: PlanVersion; chapter_id: number | null; chapter_title: string | null; chapter_number: number | null; chapter_version_id: number | null; needs_review: boolean; uses: { plan_version_id: number; chapter_version_id: number; chapter_id: number; body_version_number: number }[] }
export type PlanList = { plans: ChapterPlan[]; guidance: { id: number; content: string; material_key: string } | null }

export function ChapterPlanPanel({ bookId, chapterId, versionId }: { bookId: number; chapterId: number; versionId: number | null }) {
  const [data, setData] = useState<PlanList | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    const load = () => { void api<PlanList>(`/books/${bookId}/chapter-plans`).then(d => { if (alive) { setData(d); setError('') } }).catch(e => { if (alive) setError(String(e)) }) }
    load(); window.addEventListener('focus', load)
    return () => { alive = false; window.removeEventListener('focus', load) }
  }, [bookId, chapterId, versionId])
  const plan = data?.plans.find(p => p.chapter_id === chapterId)
  return <Panel title="本章章纲">
    {error ? <InlineError message={error} /> : !data ? <MetaText>读取中…</MetaText> : plan ? <>
      <div className="flex flex-wrap gap-2"><Badge>{plan.version.status === 'confirmed' ? '已确认' : '草稿'}</Badge>{plan.needs_review && <Badge tone="warn">正文需复核</Badge>}</div>
      <MetaText className="mt-2">{plan.version.volume} · {plan.version.title} · v{plan.version.version_number}</MetaText>
      <details className="mt-3 text-body"><summary className="cursor-pointer text-accent-700">查看章纲</summary><TextContent label="本章章纲内容" className="mt-3 leading-7" text={plan.version.content} /></details>
      <Link className="mt-3 block text-meta text-accent-700" to={`/books/${bookId}/chapter-plans/${plan.id}`}>管理章纲与关联</Link>
    </> : <><MetaText>尚未关联章纲。</MetaText><Link className="mt-2 block text-meta text-accent-700" to={`/books/${bookId}/chapter-plans`}>前往章纲页面关联已有正文</Link></>}
  </Panel>
}
