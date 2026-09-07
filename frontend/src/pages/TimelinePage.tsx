import { useEffect, useMemo, useState } from 'react'
import { Milestone, Plus } from 'lucide-react'
import { scaleLinear } from 'd3-scale'
import { api } from '../api/client'
import { useAppStore } from '../stores/useAppStore'
import type { Event } from '../types'
import { Badge, Button, Empty, Field, InlineError, Input, Loading, MetaText, PageHeader, Panel, Select, Textarea, toast } from '../lib/ui'

const tracks = [['mainline', '主线'], ['sideline', '支线'], ['foreshadow', '伏笔'], ['character', '人物线'], ['world', '世界线']]
const trackLabel = (key: string) => tracks.find(([value]) => value === key)?.[1] || key
const trackColors: Record<string, string> = { mainline: '#476e71', sideline: '#8a7fae', foreshadow: '#a97c2f', character: '#3e7c59', world: '#938d7e' }
const statusLabel: Record<string, string> = { planned: '计划中', ongoing: '进行中', resolved: '已完结', open: '开放' }

export function TimelinePage() {
  const { currentBook } = useAppStore()
  const [events, setEvents] = useState<Event[]>([])
  const [loading, setLoading] = useState(true)
  const [track, setTrack] = useState('all')
  const [selected, setSelected] = useState<Event | null>(null)
  const [form, setForm] = useState({ title: '', description: '', track: 'mainline', status: 'planned', start_chapter: '', end_chapter: '', event_type: 'other' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    if (!currentBook) return
    setLoading(true); setError('')
    try { setEvents(await api<Event[]>(`/books/${currentBook.id}/events`)) } catch (exc) { setError(String(exc)) } finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [currentBook?.id])

  const create = async () => {
    if (!currentBook || !form.title.trim()) { setError('请填写事件标题'); return }
    setBusy(true); setError('')
    try {
      const item = await api<Event>(`/books/${currentBook.id}/events`, { method: 'POST', body: JSON.stringify({ ...form, start_chapter: form.start_chapter ? Number(form.start_chapter) : null, end_chapter: form.end_chapter ? Number(form.end_chapter) : null, start_certainty: 0.8, end_certainty: form.end_chapter ? 0.8 : 0 }) })
      setEvents([...events, item]); setSelected(item)
      setForm({ title: '', description: '', track: 'mainline', status: 'planned', start_chapter: '', end_chapter: '', event_type: 'other' })
      toast(`事件「${item.title}」已加入事件轴`)
    } catch (exc) { setError(String(exc)) } finally { setBusy(false) }
  }
  const update = async () => {
    if (!selected) return
    setBusy(true); setError('')
    try {
      const updated = await api<Event>(`/events/${selected.id}`, { method: 'PUT', body: JSON.stringify({ ...selected, start_certainty: selected.start_certainty, end_certainty: selected.end_certainty }) })
      setEvents(events.map(x => x.id === updated.id ? updated : x)); setSelected(updated)
      toast('事件已更新')
    } catch (exc) { setError(String(exc)) } finally { setBusy(false) }
  }

  const filtered = events.filter(item => track === 'all' || item.track === track)
  const maxChapter = Math.max(6, ...events.map(item => item.end_chapter || item.start_chapter || 1))
  const width = 900
  const height = Math.max(260, filtered.length * 56 + 90)
  const xScale = scaleLinear().domain([1, maxChapter]).range([150, width - 50])
  const x = (chapter: number | null | undefined) => xScale(chapter || 1)
  const rows = useMemo(() => filtered.map((item, index) => ({ item, y: 56 + index * 56 })), [filtered])

  return <div className="p-6">
    <PageHeader title="事件轴" description="按章节位置排列的故事事件。未知结束时间保留开放端点，不替作者补全证据。" actions={
      <Button variant="primary" onClick={() => setSelected(null)}><Plus size={15} />新增事件</Button>} />
    <div className="mt-5 space-y-4">
      {error && <InlineError message={error} />}
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="按事件线筛选">
        {[['all', '全部'], ...tracks].map(([key, label]) => <button key={key} onClick={() => setTrack(key)}
          className={`rounded-full px-3 py-1.5 text-meta transition-colors duration-fast ${track === key ? 'bg-accent-600 text-white' : 'bg-paper-raised text-ink-soft border border-line hover:border-line-strong'}`}>
          {key !== 'all' && <i className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle" style={{ backgroundColor: trackColors[key] }} />}{label}
        </button>)}
        <span className="ml-auto text-meta text-ink-faint">点击事件条查看或编辑详情</span>
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <Panel title="事件分布" description={`共 ${filtered.length} 个事件 · 横轴为章节号`}>
          {loading ? <Loading /> : filtered.length === 0 ? <Empty icon={<Milestone size={20} />} title={track === 'all' ? '暂无事件' : `「${trackLabel(track)}」线下暂无事件`} text="从右侧添加事件，或在章节分析后确认带标题的事件候选。" /> :
            <div className="overflow-x-auto scrollbar-thin">
              <svg viewBox={`0 0 ${width} ${height}`} className="w-full min-w-[720px]" role="img" aria-label="事件时间轴">
                <line x1="150" y1="30" x2={width - 50} y2="30" stroke="#d5cfbd" />
                {Array.from({ length: maxChapter }, (_, index) => <g key={index}>
                  <line x1={x(index + 1)} y1="30" x2={x(index + 1)} y2={height - 20} stroke="#efebe0" strokeDasharray="3 5" />
                  <text x={x(index + 1)} y="20" textAnchor="middle" fontSize="11" fill="#938d7e">{index + 1}</text>
                </g>)}
                {rows.map(({ item, y }) => {
                  const color = trackColors[item.track] || '#476e71'
                  const active = selected?.id === item.id
                  return <g key={item.id} className="cursor-pointer" opacity={active ? 1 : 0.88} onClick={() => setSelected(item)}>
                    <text x="8" y={y + 4} fontSize="12" fill={active ? '#314a4d' : '#5f5a4e'} fontWeight={active ? 600 : 400}>{item.title.length > 10 ? `${item.title.slice(0, 10)}…` : item.title}</text>
                    {item.end_chapter
                      ? <rect x={x(item.start_chapter)} y={y - 8} width={Math.max(10, x(item.end_chapter) - x(item.start_chapter))} height="16" rx="8" fill={color} opacity={active ? 1 : 0.75} stroke={active ? '#2b2822' : 'none'} strokeWidth={active ? 1 : 0} />
                      : <>
                        <path d={`M ${x(item.start_chapter)} ${y} l 8 -8 l 8 8 l -8 8 z`} fill={color} />
                        <line x1={x(item.start_chapter) + 15} y1={y} x2={width - 50} y2={y} stroke={color} strokeDasharray="5 4" opacity="0.55" />
                      </>}
                  </g>
                })}
              </svg>
            </div>}
        </Panel>

        <Panel title={selected ? `编辑：${selected.title}` : '新增事件'} className="self-start"
          description={selected ? `创建于第 ${selected.start_chapter || '?'} 章 · ${statusLabel[selected.status] || selected.status}` : undefined}
          action={selected && <Button size="sm" variant="ghost" onClick={() => setSelected(null)}>转为新增</Button>}>
          {selected && <div className="mb-3 flex flex-wrap items-center gap-1.5">
            <Badge tone="accent">{trackLabel(selected.track)}</Badge>
            <Badge>{statusLabel[selected.status] || selected.status}</Badge>
          </div>}
          <div className="space-y-3.5">
            <Field label="事件标题"><Input value={selected ? selected.title : form.title} onChange={e => selected ? setSelected({ ...selected, title: e.target.value }) : setForm({ ...form, title: e.target.value })} placeholder="例如：陈渡夜探火窟" /></Field>
            <Field label="事件描述"><Textarea rows={3} value={selected ? selected.description : form.description} onChange={e => selected ? setSelected({ ...selected, description: e.target.value }) : setForm({ ...form, description: e.target.value })} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="起始章"><Input type="number" min={1} value={selected ? String(selected.start_chapter || '') : form.start_chapter} onChange={e => selected ? setSelected({ ...selected, start_chapter: Number(e.target.value) || null }) : setForm({ ...form, start_chapter: e.target.value })} /></Field>
              <Field label="结束章" hint="未知可留空"><Input type="number" min={1} value={selected ? String(selected.end_chapter || '') : form.end_chapter} onChange={e => selected ? setSelected({ ...selected, end_chapter: Number(e.target.value) || null }) : setForm({ ...form, end_chapter: e.target.value })} /></Field>
            </div>
            {!selected && <Field label="所属事件线">
              <Select value={form.track} onChange={e => setForm({ ...form, track: e.target.value })}>
                {tracks.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </Select>
            </Field>}
            {selected && (selected.objective_fact || selected.hidden_truth) && <div className="rounded-md bg-paper-sunken px-3 py-2.5 text-meta leading-6 text-ink-soft">
              {selected.objective_fact && <div><span className="text-ink-faint">客观事实：</span>{selected.objective_fact}</div>}
              {selected.hidden_truth && <div><span className="text-ink-faint">隐藏真相：</span>{selected.hidden_truth}</div>}
            </div>}
            <Button variant="primary" className="w-full" disabled={busy} onClick={() => void (selected ? update() : create())}>{busy ? '保存中…' : selected ? '保存事件' : '添加事件'}</Button>
          </div>
        </Panel>
      </div>
    </div>
  </div>
}
