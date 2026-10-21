import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowDown, ArrowUp, Plus, Save } from 'lucide-react'
import { api, API_BASE } from '../api/client'
import { useAppStore } from '../stores/useAppStore'
import { useUnsavedChanges } from '../lib/useUnsavedChanges'
import { TextContent, TextEditor } from '../lib/TextContent'
import { Badge, Button, Empty, Field, InlineError, Input, Loading, MetaText, Modal, PageHeader, Panel, Select, toast } from '../lib/ui'
import type { ChapterPlan, PlanList, PlanVersion } from '../lib/ChapterPlanPanel'
import type { Chapter } from '../types'
import { PlanContinuation } from '../lib/PlanContinuation'
import { WritingTaskControl } from '../lib/WritingTaskControl'
import { ChapterGenerationButton } from '../lib/ChapterGenerationModal'

const blank = () => ({ title: '', volume: '第一卷', content: '', status: 'draft' as 'draft' | 'confirmed' })
const fields = (p: ChapterPlan) => ({ title: p.version.title, volume: p.version.volume, content: p.version.content, status: p.version.status })

export function ChapterPlansPage() {
  const { currentBook } = useAppStore()
  const { planId } = useParams()
  const navigate = useNavigate()
  const bid = currentBook!.id
  const base = `/books/${bid}/chapter-plans`
  const [data, setData] = useState<PlanList>({ plans: [], guidance: null })
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [draft, setDraft] = useState(blank)
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [history, setHistory] = useState<PlanVersion[]>([])
  const [preview, setPreview] = useState<PlanVersion | null>(null)
  const [binding, setBinding] = useState('')
  const [volumeFilter, setVolumeFilter] = useState('')
  const [writingBusy, setWritingBusy] = useState(false)
  const [generationBusy, setGenerationBusy] = useState(false)
  const [planBusy, setPlanBusy] = useState(false)
  const seq = useRef(0)
  const listRef = useRef<HTMLDivElement>(null)
  const wasWriting = useRef(false)
  const current = data.plans.find(p => p.id === selected)
  const dirty = JSON.stringify(draft) !== JSON.stringify(current && !creating ? fields(current) : blank())
  const live = useRef({ dirty, selected, creating })
  live.current = { dirty, selected, creating }
  useUnsavedChanges(dirty)
  const choose = (p: ChapterPlan) => { setSelected(p.id); setDraft(fields(p)); setBinding(p.chapter_id ? String(p.chapter_id) : ''); setCreating(false); setPreview(null) }
  const accept = (p: ChapterPlan) => { if (volumeFilter && volumeFilter !== p.version.volume) setVolumeFilter(''); setData(d => ({ ...d, plans: d.plans.some(x => x.id === p.id) ? d.plans.map(x => x.id === p.id ? p : x) : [...d.plans, p] })); choose(p) }
  useEffect(() => {
    const list = listRef.current
    if (!list || !selected) return
    const row = Array.from(list.querySelectorAll<HTMLElement>('[data-plan-row]')).find(el => el.dataset.planRow === selected)
    if (!row) return
    const bounds = list.getBoundingClientRect(), item = row.getBoundingClientRect()
    if (item.top < bounds.top) list.scrollTop += item.top - bounds.top
    else if (item.bottom > bounds.bottom) list.scrollTop += item.bottom - bounds.bottom
  }, [selected, loading, volumeFilter, data.plans])
  useEffect(() => {
    const token = ++seq.current
    setLoading(true)
    void Promise.all([api<PlanList>(base), api<Chapter[]>(`/books/${bid}/chapters?include_content=false`)]).then(([d, c]) => {
      if (seq.current !== token) return
      setData(d); setChapters(c)
      const p = planId ? d.plans.find(p => p.id === planId) : d.plans[0]
      if (p) choose(p)
      else { setSelected(null); setCreating(false); setDraft(blank()) }
      setError('')
    }).catch(e => { if (seq.current === token) setError(String(e)) }).finally(() => { if (seq.current === token) setLoading(false) })
    return () => { seq.current++ }
  }, [base, bid, planId])
  useEffect(() => {
    let alive = true
    setHistory([])
    if (current && !creating) void api<PlanVersion[]>(`${base}/${current.id}/versions`).then(v => { if (alive) setHistory(v) }).catch(e => { if (alive) setError(String(e)) })
    return () => { alive = false }
  }, [base, current?.id, current?.version.id, creating])
  useEffect(() => {
    const finished = wasWriting.current && !writingBusy
    wasWriting.current = writingBusy
    if (!finished) return
    let alive = true
    void Promise.all([api<PlanList>(base), api<Chapter[]>(`/books/${bid}/chapters?include_content=false`)]).then(([d, c]) => {
      if (!alive || live.current.dirty) return
      setData(d); setChapters(c)
      const p = d.plans.find(p => p.id === live.current.selected)
      if (p && !live.current.creating) choose(p)
    }).catch(e => { if (alive) setError(String(e)) })
    return () => { alive = false }
  }, [writingBusy, base, bid])
  const act = async (fn: () => Promise<void>) => {
    if (busy) return
    setBusy(true); setError('')
    try { await fn() } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  const discardOK = () => !dirty || window.confirm('有未保存修改，确定放弃后切换？')
  const save = () => act(async () => {
    const p = await api<ChapterPlan>(current && !creating ? `${base}/${current.id}/versions` : base, { method: 'POST', body: JSON.stringify({ ...draft, ...(current && !creating ? { base_version_id: current.version.id } : {}) }) })
    accept(p); toast(`章纲已保存为 v${p.version.version_number}`)
  })
  const move = (p: ChapterPlan, offset: number) => act(async () => {
    const ids = data.plans.map(x => x.id), from = ids.indexOf(p.id), to = from + offset
    if (to < 0 || to >= ids.length) return
    const next = [...ids]; [next[from], next[to]] = [next[to], next[from]]
    setData(await api<PlanList>(`${base}/reorder`, { method: 'POST', body: JSON.stringify({ plan_ids: next, previous_order: ids }) }))
  })
  const refreshSelected = async () => {
    const [d, c] = await Promise.all([api<PlanList>(base), api<Chapter[]>(`/books/${bid}/chapters?include_content=false`)])
    setData(d); setChapters(c)
    const p = d.plans.find(x => x.id === live.current.selected)
    if (p && !live.current.dirty && !live.current.creating) choose(p)
  }
  const boundChapter = current?.chapter_id ? chapters.find(chapter => chapter.id === current.chapter_id) : null
  const editor = (current || creating) ? <div className="min-w-0 space-y-4">
        <Panel className="[&>header]:flex-wrap" title={creating ? '新章纲' : `章纲 · v${current?.version.version_number}`} description={current && !creating ? `第${current.position}章 · ${current.version.title}` : undefined} action={<div className="flex flex-wrap gap-2"><Button variant="primary" disabled={busy || !draft.title.trim() || !draft.content.trim() || (!dirty && !creating)} onClick={() => void save()}><Save size={14} />保存章纲</Button>{current && !creating && <PlanContinuation bookId={bid} label="重写本章章纲" modalTitle="重写本章章纲" initialMode="regenerate" initialStartPosition={current.position} initialCount={1}
          disabled={busy || dirty || writingBusy || generationBusy || planBusy} onBusy={setPlanBusy} onUpdated={refreshSelected} />}{current && !creating && <ChapterGenerationButton bookId={bid} startChapter={current.position} startTitle={current.version.title}
          hasBody={!!boundChapter?.word_count} label={boundChapter?.word_count ? '重写本章正文' : '生成正文'}
          disabled={busy || dirty || writingBusy || generationBusy} onBusy={setGenerationBusy}
          onOpenChapter={id => navigate(`/books/${bid}/reader?chapter=${id}`)} />}</div>}>
          {dirty && <div className="mb-3"><Badge tone="warn">有未保存修改</Badge></div>}
          <div className="grid gap-4 sm:grid-cols-2"><Field label="所属卷"><Input value={draft.volume} disabled={busy} onChange={e => setDraft(d => ({ ...d, volume: e.target.value }))} /></Field><Field label="拟定章节名"><Input value={draft.title} disabled={busy} onChange={e => setDraft(d => ({ ...d, title: e.target.value }))} /></Field></div>
          <div className="mt-4"><Field label="章纲状态" hint="草稿也可从正文页生成；确认用于记录章纲已审阅。持续生成仍按已确认章纲执行。"><Select value={draft.status} disabled={busy} onChange={e => setDraft(d => ({ ...d, status: e.target.value as 'draft' | 'confirmed' }))}><option value="draft">草稿</option><option value="confirmed">已确认</option></Select></Field></div>
          <div className="mt-4"><TextEditor aria-label="章纲内容" className="paper-text min-h-[45vh] w-full" disabled={busy} value={draft.content} onChange={e => setDraft(d => ({ ...d, content: e.target.value }))} placeholder="本章目标、冲突、主要行动、实际结果、结尾悬念…" /></div>
          {current && <a className="mt-3 inline-block text-meta text-accent-700" href={`${API_BASE}${base}/${current.id}/export.md`} download>导出 Markdown</a>}
        </Panel>
        {current && !creating && <Panel title="正文关联">
          <MetaText>关联固定章节，不随名称或排序变化。手动关联不会伪造生成来源。</MetaText>
          <div className="mt-3 flex flex-wrap gap-2"><Select aria-label="关联正文" value={binding} disabled={busy || dirty} onChange={e => setBinding(e.target.value)}><option value="">不关联正文</option>{chapters.map(c => <option key={c.id} value={c.id}>第{c.chapter_number}章 {c.title}</option>)}</Select><Button disabled={busy || dirty || binding === String(current.chapter_id ?? '')} onClick={() => void act(async () => { accept(await api<ChapterPlan>(`${base}/${current.id}/binding`, { method: 'POST', body: JSON.stringify({ base_version_id: current.version.id, previous_chapter_id: current.chapter_id, chapter_id: binding ? Number(binding) : null }) })); toast('关联已保存') })}>保存关联</Button></div>
          {current.chapter_id && <div className="mt-3 flex flex-wrap items-center gap-3"><Link className="text-accent-700" to={`/books/${bid}/reader?chapter=${current.chapter_id}`}>打开第{current.chapter_number}章正文</Link>{current.needs_review ? <><Badge tone="warn">正文需复核</Badge><Button size="sm" disabled={busy || dirty} onClick={() => void act(async () => { accept(await api<ChapterPlan>(`${base}/${current.id}/review`, { method: 'POST', body: JSON.stringify({ base_version_id: current.version.id, chapter_version_id: current.chapter_version_id }) })); toast('已记录人工核对') })}>确认正文</Button></> : <Badge tone="ok">已核对当前版本</Badge>}</div>}
          {current.uses.length > 0 && <details className="mt-3 text-meta text-ink-soft"><summary className="cursor-pointer">生成使用记录</summary>{current.uses.map(u => <div key={u.chapter_version_id} className="mt-2">正文版本 v{u.body_version_number} 使用章纲 {history.find(h => h.id === u.plan_version_id)?.version_number ? `v${history.find(h => h.id === u.plan_version_id)!.version_number}` : `版本 ${u.plan_version_id}`}</div>)}</details>}
        </Panel>}
        {current && !creating && <Panel title="章纲历史"><div className="flex flex-wrap gap-2">{history.map(v => <Button key={v.id} size="sm" onClick={() => setPreview(v)}>查看 v{v.version_number}</Button>)}</div></Panel>}
      </div> : null
  if (loading) return <Loading text="正在读取章纲…" />
  return <div className="w-full min-w-0 p-4 lg:p-6">
    <PageHeader title="章纲" description="从列表选择章纲，查看详情或继续编辑。" actions={<>
      <PlanContinuation bookId={bid} disabled={dirty || busy || writingBusy} onBusy={setPlanBusy} onUpdated={async () => {
        const d = await api<PlanList>(base)
        setData(d)
        if (!live.current.dirty && !live.current.creating) {
          const p = d.plans.find(p => p.id === live.current.selected)
          if (p) choose(p)
        }
      }} />
      <WritingTaskControl bookId={bid} disabled={dirty || busy || planBusy} onBusy={setWritingBusy} onOpen={id => { navigate(`/books/${bid}/reader?chapter=${id}`) }} />
      <Button disabled={busy || writingBusy || planBusy} onClick={() => { if (discardOK()) { setCreating(true); setSelected(null); setDraft(blank()); setHistory([]) } }}><Plus size={14} />新建章纲</Button>
    </>} />
    {error && <div className="mt-4"><InlineError message={error} /></div>}
    <div className="mt-5 grid min-w-0 items-start gap-5 lg:grid-cols-[18rem_minmax(0,1fr)] xl:grid-cols-[20rem_minmax(0,1fr)]">
    <section aria-label="章纲列表" className="min-w-0 overflow-hidden rounded-lg border border-line bg-paper-raised lg:sticky lg:top-4">
      <div className="space-y-3 border-b border-line-soft p-3">
        <div className="flex items-center justify-between"><h2 className="font-semibold text-ink">章纲列表</h2><MetaText>{data.plans.length} 份</MetaText></div>
        <Field label="按卷查看"><Select value={volumeFilter} disabled={busy} onChange={e => {
          if (!discardOK()) return
          const volume = e.target.value
          setVolumeFilter(volume)
          const visible = data.plans.filter(p => !volume || p.version.volume === volume)
          const next = visible.find(p => p.id === selected) || visible[0]
          if (next) choose(next)
          else { setSelected(null); setCreating(false); setDraft(blank()); setPreview(null) }
        }}><option value="">全部卷 · {data.plans.length} 份</option>{[...new Set(data.plans.map(p => p.version.volume))].map(v => <option key={v}>{v}</option>)}</Select></Field>
      </div>
      <div ref={listRef} className="max-h-64 overflow-y-auto overscroll-contain lg:max-h-[calc(100dvh-16rem)]">
      {!data.plans.length && <Empty compact title="尚无章纲" text="先写下第一章的目标与冲突。" />}
      {data.plans.filter(p => !volumeFilter || p.version.volume === volumeFilter).map(p => {
        const active = selected === p.id && !creating
        return <div key={p.id} className="border-b border-line-soft last:border-b-0" data-plan-row={p.id}>
          <div className={`flex min-w-0 items-center gap-1 border-l-2 pr-2 ${active ? 'border-accent-600 bg-accent-50' : 'border-transparent hover:bg-paper-sunken'}`}>
            <button aria-current={active ? 'true' : undefined} aria-controls="chapter-plan-detail" aria-label={`章纲 ${p.position} ${p.version.title}`}
              className="min-w-0 flex-1 px-3 py-3 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-500" disabled={busy} onClick={() => {
                if (active) return
                if (!discardOK()) return
                choose(p)
              }}>
              <span className="flex min-w-0 items-center gap-2">
                <span className="shrink-0 text-meta tabular-nums text-ink-soft">{p.position}</span>
                <span className={`min-w-0 truncate text-body ${active ? 'font-semibold text-accent-800' : 'text-ink'}`} title={p.version.title}>{p.version.title}</span>
              </span>
              <span className="mt-1.5 flex flex-wrap gap-1">
                <Badge tone={p.version.status === 'confirmed' ? 'accent' : 'neutral'}>{p.version.status === 'confirmed' ? '已确认' : '草稿'}</Badge>
                {p.chapter_id ? <Badge tone={p.needs_review ? 'warn' : 'ok'}>{p.needs_review ? '正文需复核' : '已有正文'}</Badge> : <Badge>未生成</Badge>}
              </span>
            </button>
            <div className="flex shrink-0 flex-col">
            <Button size="sm" variant="ghost" aria-label={`上移${p.version.title}`} disabled={busy || dirty || p.position === 1} onClick={() => void move(p, -1)}><ArrowUp size={13} /></Button>
            <Button size="sm" variant="ghost" aria-label={`下移${p.version.title}`} disabled={busy || dirty || p.position === data.plans.length} onClick={() => void move(p, 1)}><ArrowDown size={13} /></Button>
            </div>
          </div>
        </div>
      })}
      </div>
    </section>
    <section id="chapter-plan-detail" aria-label="章纲详情" className="min-w-0 space-y-4">
      {data.guidance && <details className="rounded-lg border border-line bg-paper-raised p-4 text-body">
        <summary className="cursor-pointer font-medium text-accent-700">共同执行说明与迁移勘误</summary>
        <TextContent label="章纲共同说明" className="mt-4 leading-7" text={data.guidance.content} />
        <Link to={`/books/${bid}/materials/${data.guidance.material_key}`} className="mt-3 block text-accent-700">编辑共同说明（保留版本历史）</Link>
      </details>}
      {editor || <Panel><Empty title={data.plans.length ? '请选择一份章纲' : '开始规划第一章'} text={data.plans.length ? '选择列表中的章纲，在这里查看内容、编辑或关联正文。' : '点击“新建章纲”，在这里填写章节目标与情节。'} /></Panel>}
    </section>
    </div>
    <Modal open={!!preview} onClose={() => setPreview(null)} title={`章纲历史 · v${preview?.version_number}`} width="max-w-4xl" footer={<>
      <Button variant="ghost" onClick={() => setPreview(null)}>关闭</Button>
      <Button disabled={busy || dirty || !preview || preview.id === current?.version.id} onClick={() => void act(async () => {
        if (!current || !preview) return
        accept(await api<ChapterPlan>(`${base}/${current.id}/versions/${preview.id}/restore`, { method: 'POST', body: JSON.stringify({ base_version_id: current.version.id }) })); setPreview(null); toast('已恢复为新的草稿版本')
      })}>恢复为新草稿版本</Button>
    </>}><MetaText>{preview?.volume} · {preview?.title}</MetaText><TextContent label="历史章纲" className="paper-text mt-4" text={preview?.content || ''} /></Modal>
  </div>
}
