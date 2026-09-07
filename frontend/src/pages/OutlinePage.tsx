import { useEffect, useState } from 'react'
import { ListTree, Plus } from 'lucide-react'
import { api } from '../api/client'
import { useAppStore } from '../stores/useAppStore'
import type { Outline, OutlineBlock } from '../types'
import { Badge, Button, Empty, Field, InlineError, Input, Loading, MetaText, PageHeader, Panel, SectionTitle, Textarea, toast } from '../lib/ui'

const blockTypeLabel: Record<string, string> = { volume: '卷', arc: '篇', stage: '阶段', scene: '场景', chapter_plan: '章节计划' }

export function OutlinePage() {
  const { currentBook } = useAppStore()
  const [outlines, setOutlines] = useState<Outline[]>([])
  const [blocks, setBlocks] = useState<OutlineBlock[]>([])
  const [selected, setSelected] = useState<Outline | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [outlineForm, setOutlineForm] = useState({ title: '', description: '' })
  const [blockForm, setBlockForm] = useState({ title: '', summary: '', objective: '', conflict: '', ending: '', start_chapter: '', end_chapter: '' })
  const [busy, setBusy] = useState(false)

  const loadBlocks = async (outline: Outline) => {
    setSelected(outline)
    try { setBlocks(await api<OutlineBlock[]>(`/outlines/${outline.id}/blocks`)) } catch (exc) { setError(String(exc)) }
  }
  const load = async () => {
    if (!currentBook) return
    setLoading(true); setError('')
    try {
      const data = await api<Outline[]>(`/books/${currentBook.id}/outlines`)
      setOutlines(data)
      if (data[0]) await loadBlocks(data[0])
      else { setSelected(null); setBlocks([]) }
    } catch (exc) { setError(String(exc)) } finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [currentBook?.id])

  const createOutline = async () => {
    if (!currentBook || !outlineForm.title.trim()) { setError('请填写大纲名称'); return }
    setBusy(true); setError('')
    try {
      const item = await api<Outline>(`/books/${currentBook.id}/outlines`, { method: 'POST', body: JSON.stringify(outlineForm) })
      setOutlines([...outlines, item]); setOutlineForm({ title: '', description: '' })
      setBlocks([]); setSelected(item); toast(`大纲「${item.title}」已创建`)
    } catch (exc) { setError(String(exc)) } finally { setBusy(false) }
  }
  const createBlock = async () => {
    if (!selected || !blockForm.title.trim()) { setError('请填写大纲块标题'); return }
    setBusy(true); setError('')
    try {
      const item = await api<OutlineBlock>(`/outlines/${selected.id}/blocks`, {
        method: 'POST',
        body: JSON.stringify({ title: blockForm.title, summary: blockForm.summary, objective: blockForm.objective, conflict: blockForm.conflict, ending: blockForm.ending, start_chapter: blockForm.start_chapter ? Number(blockForm.start_chapter) : null, end_chapter: blockForm.end_chapter ? Number(blockForm.end_chapter) : null, order_index: blocks.length }),
      })
      setBlocks([...blocks, item]); setBlockForm({ title: '', summary: '', objective: '', conflict: '', ending: '', start_chapter: '', end_chapter: '' })
      toast(`大纲块「${item.title}」已加入`)
    } catch (exc) { setError(String(exc)) } finally { setBusy(false) }
  }

  return <div className="p-6">
    <PageHeader title="大纲" description="用轻量分块记录卷、篇、阶段和章节计划；提示词组合会引用大纲位置。" />
    <div className="mt-5 space-y-4">
      {error && <InlineError message={error} />}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <div className="space-y-4 self-start">
          <Panel title={`大纲版本 · ${outlines.length}`} bodyClassName="max-h-72 overflow-y-auto scrollbar-thin">
            {loading ? <Loading /> : outlines.length === 0 ? <Empty compact icon={<ListTree size={18} />} title="还没有大纲" text="先在下方新建一个大纲版本。" /> : <div className="space-y-1">
              {outlines.map(item => <button key={item.id} onClick={() => void loadBlocks(item)} className={`w-full rounded-md border-l-2 px-3 py-2.5 text-left transition-colors duration-fast ${selected?.id === item.id ? 'border-accent-600 bg-accent-50' : 'border-transparent hover:bg-paper-sunken'}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium text-ink">{item.title}</span>
                  <Badge tone="accent">v{item.version}</Badge>
                </div>
                {item.description && <MetaText className="mt-0.5 line-clamp-1">{item.description}</MetaText>}
              </button>)}
            </div>}
          </Panel>
          <Panel title="新建大纲版本">
            <div className="space-y-3">
              <Field label="名称"><Input value={outlineForm.title} onChange={e => setOutlineForm({ ...outlineForm, title: e.target.value })} placeholder="例如：第一卷大纲" /></Field>
              <Field label="说明"><Textarea rows={2} value={outlineForm.description} onChange={e => setOutlineForm({ ...outlineForm, description: e.target.value })} /></Field>
              <Button variant="primary" className="w-full" disabled={busy} onClick={() => void createOutline()}><Plus size={15} />创建大纲</Button>
            </div>
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title={selected ? `「${selected.title}」的大纲块` : '大纲块'} description={selected ? `${blocks.length} 个块，按添加顺序排列` : '先选择或新建一个大纲版本'}>
            {!selected ? <Empty compact title="未选择大纲" /> : blocks.length === 0 ? <Empty compact title="还没有大纲块" text="在下方添加第一个块：一个卷、一个阶段，或一段时间的章节计划。" /> : <ol className="relative space-y-3 border-l-2 border-line pl-5">
              {blocks.map((block, index) => <li key={block.id} className="relative">
                <span className="absolute -left-[27px] top-4 h-3 w-3 rounded-full border-2 border-paper-raised bg-accent-400" />
                <div className="rounded-md border border-line-soft bg-paper px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-ink">{index + 1}. {block.title}</span>
                    {block.block_type && blockTypeLabel[block.block_type] && <Badge>{blockTypeLabel[block.block_type]}</Badge>}
                    {(block.start_chapter || block.end_chapter) && <Badge tone="accent">第 {block.start_chapter || '?'} – {block.end_chapter || '?'} 章</Badge>}
                  </div>
                  {block.summary && <p className="mt-1.5 text-body leading-6 text-ink-soft">{block.summary}</p>}
                  <div className="mt-2 grid grid-cols-1 gap-2 text-meta text-ink-faint md:grid-cols-3">
                    {block.objective && <span><span className="text-ink-soft">目标：</span>{block.objective}</span>}
                    {block.conflict && <span><span className="text-ink-soft">冲突：</span>{block.conflict}</span>}
                    {block.ending && <span><span className="text-ink-soft">转折 / 结尾：</span>{block.ending}</span>}
                  </div>
                </div>
              </li>)}
            </ol>}
          </Panel>
          {selected && <Panel title={`向「${selected.title}」添加大纲块`}>
            <div className="space-y-4">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <Field label="标题"><Input value={blockForm.title} onChange={e => setBlockForm({ ...blockForm, title: e.target.value })} placeholder="例如：初入京城" /></Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="起始章"><Input type="number" min={1} value={blockForm.start_chapter} onChange={e => setBlockForm({ ...blockForm, start_chapter: e.target.value })} /></Field>
                  <Field label="结束章"><Input type="number" min={1} value={blockForm.end_chapter} onChange={e => setBlockForm({ ...blockForm, end_chapter: e.target.value })} /></Field>
                </div>
              </div>
              <Field label="概要"><Textarea rows={2} value={blockForm.summary} onChange={e => setBlockForm({ ...blockForm, summary: e.target.value })} /></Field>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                <Field label="目标"><Textarea rows={2} value={blockForm.objective} onChange={e => setBlockForm({ ...blockForm, objective: e.target.value })} /></Field>
                <Field label="冲突"><Textarea rows={2} value={blockForm.conflict} onChange={e => setBlockForm({ ...blockForm, conflict: e.target.value })} /></Field>
                <Field label="转折 / 结尾"><Textarea rows={2} value={blockForm.ending} onChange={e => setBlockForm({ ...blockForm, ending: e.target.value })} /></Field>
              </div>
              <Button variant="primary" disabled={busy} onClick={() => void createBlock()}><Plus size={15} />添加大纲块</Button>
            </div>
          </Panel>}
        </div>
      </div>
    </div>
  </div>
}
