import { useEffect, useState } from 'react'
import { History, Plus, ScrollText, Search } from 'lucide-react'
import { api } from '../api/client'
import { useAppStore } from '../stores/useAppStore'
import type { Knowledge } from '../types'
import { Badge, Button, Empty, Field, InlineError, Input, Loading, MetaText, Modal, PageHeader, Panel, Select, Textarea, toast } from '../lib/ui'

const categories: [string, string][] = [['worldview', '世界观'], ['rule', '规则'], ['item', '物品'], ['location', '地点'], ['faction', '势力'], ['history', '历史'], ['other', '其他']]
const categoryLabel = (key: string) => categories.find(([value]) => value === key)?.[1] || key

type KnowledgeVersion = { id: number; knowledge_item_id: number; version_number: number; content: string; change_reason: string; source: string; created_at: string }

export function KnowledgePage() {
  const { currentBook } = useAppStore()
  const [items, setItems] = useState<Knowledge[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Knowledge | null>(null)
  const [form, setForm] = useState({ category: 'worldview', name: '', content: '', status: 'active' })
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [versions, setVersions] = useState<KnowledgeVersion[]>([])
  const [showVersions, setShowVersions] = useState(false)

  const load = async () => {
    if (!currentBook) return
    setLoading(true); setError('')
    try { setItems(await api<Knowledge[]>(`/books/${currentBook.id}/knowledge`)) } catch (exc) { setError(String(exc)) } finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [currentBook?.id])

  const edit = (item: Knowledge | null) => {
    setSelected(item)
    setForm(item ? { category: item.category, name: item.name, content: item.content, status: item.status } : { category: 'worldview', name: '', content: '', status: 'active' })
    setError('')
  }
  const save = async () => {
    if (!currentBook || !form.name.trim()) { setError('请填写资料名称'); return }
    setSaving(true); setError('')
    try {
      const item = selected
        ? await api<Knowledge>(`/knowledge/${selected.id}`, { method: 'PUT', body: JSON.stringify(form) })
        : await api<Knowledge>(`/books/${currentBook.id}/knowledge`, { method: 'POST', body: JSON.stringify(form) })
      setItems(selected ? items.map(x => x.id === item.id ? item : x) : [...items, item])
      edit(item); toast(selected ? `「${item.name}」已保存，旧版本已归档` : `资料「${item.name}」已创建`)
    } catch (exc) { setError(String(exc)); toast('保存资料失败', 'danger') } finally { setSaving(false) }
  }
  const openVersions = async () => {
    if (!selected) return
    try { setVersions(await api<KnowledgeVersion[]>(`/knowledge/${selected.id}/versions`)); setShowVersions(true) } catch (exc) { setError(String(exc)) }
  }

  const filtered = items.filter(x => (filter === 'all' || x.category === filter) && `${x.name}${x.content}`.includes(query))
  const countOf = (key: string) => key === 'all' ? items.length : items.filter(x => x.category === key).length
  // 分类筛选项 = 预置分类 + 数据中实际出现的自定义分类（自动入库可能产生新分类）
  const activeCategories: [string, string][] = [['all', '全部'], ...categories]
  for (const item of items) if (!activeCategories.some(([key]) => key === item.category)) activeCategories.push([item.category, item.category])

  return <div className="p-6">
    <PageHeader title="世界观与知识" description="资料库是全书的事实源，分析结果只有经确认或无冲突时才会写入这里。" actions={
      <Button variant="primary" onClick={() => edit(null)}><Plus size={15} />新建资料</Button>} />
    <div className="mt-5 space-y-4">
      {error && <InlineError message={error} />}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <Panel title={`资料条目 · ${filtered.length}`} className="self-start" bodyClassName="max-h-[70vh] overflow-y-auto scrollbar-thin">
          <div className="relative mb-3">
            <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint" />
            <Input className="py-1.5 pl-7 text-meta" placeholder="搜索名称或内容" value={query} onChange={e => setQuery(e.target.value)} aria-label="搜索资料" />
          </div>
          <div className="mb-3 flex flex-wrap gap-1" role="group" aria-label="按分类筛选">
            {activeCategories.map(([key, label]) => <button key={key} onClick={() => setFilter(key)}
              className={`rounded-full px-2.5 py-1 text-meta transition-colors duration-fast ${filter === key ? 'bg-accent-600 text-white' : 'bg-paper-sunken text-ink-soft hover:bg-line-soft'}`}>
              {label} <span className="tabular-nums opacity-70">{countOf(key)}</span>
            </button>)}
          </div>
          {loading ? <Loading /> : filtered.length === 0 ? <Empty compact icon={<ScrollText size={18} />} title={query || filter !== 'all' ? '没有匹配的资料' : '暂无资料'} text="先添加世界观、规则或知识卡。" /> : <div className="space-y-1">
            {filtered.map(item => <button key={item.id} onClick={() => edit(item)} className={`w-full rounded-md border-l-2 px-3 py-2.5 text-left transition-colors duration-fast ${selected?.id === item.id ? 'border-accent-600 bg-accent-50' : 'border-transparent hover:bg-paper-sunken'}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium text-ink">{item.name}</span>
                <Badge tone="accent">{categoryLabel(item.category)}</Badge>
              </div>
              <MetaText className="mt-0.5 line-clamp-1">{item.content || '暂无内容'}</MetaText>
            </button>)}
          </div>}
        </Panel>

        <Panel title={selected ? `编辑：${selected.name}` : '新建资料'} description={selected ? `最近更新 ${new Date(selected.updated_at).toLocaleString()}` : undefined}
          action={selected && <Button size="sm" variant="ghost" onClick={() => void openVersions()}><History size={13} />历史版本</Button>}>
          <div className="max-w-2xl space-y-4">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <Field label="名称"><Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="例如：火窟、南衙禁军" /></Field>
              <Field label="分类">
                <Select value={form.category} onChange={e => setForm({ ...form, category: e.target.value })}>
                  {categories.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                </Select>
              </Field>
            </div>
            <Field label="内容" hint="尽量写可验证的设定事实，分析时会以此为参照边界。">
              <Textarea rows={10} value={form.content} onChange={e => setForm({ ...form, content: e.target.value })} placeholder="这条设定是什么、有哪些约束、和哪些角色或事件有关…" />
            </Field>
            <div className="flex items-center gap-3 border-t border-line-soft pt-4">
              <Button variant="primary" disabled={saving} onClick={() => void save()}>{saving ? '保存中…' : selected ? '保存修改' : '创建资料'}</Button>
            </div>
          </div>
        </Panel>
      </div>
    </div>

    <Modal open={showVersions} onClose={() => setShowVersions(false)} title={`「${selected?.name}」的历史版本`} description="每次保存或自动入库都会生成一个版本，这里只读查看。" width="max-w-3xl"
      footer={<Button variant="ghost" onClick={() => setShowVersions(false)}>关闭</Button>}>
      <div className="space-y-3">
        {versions.length === 0 && <Empty compact title="暂无历史版本" />}
        {versions.map(version => <div key={version.id} className="rounded-md border border-line-soft px-4 py-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-body font-medium text-ink">v{version.version_number}</span>
            <MetaText>{version.source}{version.change_reason && ` · ${version.change_reason}`} · {new Date(version.created_at).toLocaleString()}</MetaText>
          </div>
          <p className="mt-2 whitespace-pre-wrap break-words text-body leading-6 text-ink-soft">{version.content || '（空）'}</p>
        </div>)}
      </div>
    </Modal>
  </div>
}
