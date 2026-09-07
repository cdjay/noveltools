import { useEffect, useState } from 'react'
import { History, Search, UserPlus, Users } from 'lucide-react'
import { api } from '../api/client'
import { useAppStore } from '../stores/useAppStore'
import type { Character } from '../types'
import { Badge, Button, ConfirmModal, Empty, Field, InlineError, Input, Loading, MetaText, Modal, PageHeader, Panel, SectionTitle, Select, StatusBadge, Textarea, toast } from '../lib/ui'

type CharacterForm = { name: string; aliases: string; role: string; age: string; identity: string; summary: string; appearance: string; personality: string; level: string; inventory: string; relationships: string; goals: string; fears: string; abilities: string; secrets: string; current_state: string; status: string }
const emptyForm: CharacterForm = { name: '', aliases: '', role: '', age: '', identity: '', summary: '', appearance: '', personality: '', level: '', inventory: '', relationships: '', goals: '', fears: '', abilities: '', secrets: '', current_state: '', status: 'active' }

type CharacterVersion = { id: number; character_id: number; version_number: number; structured_json: Record<string, unknown>; change_reason: string; source: string; created_at: string }

const fieldLabels: Record<string, string> = { name: '姓名', aliases: '别名', role: '身份 / 定位', age: '年龄', identity: '身份描述', summary: '简介', appearance: '外貌', personality: '性格', level: '等级 / 境界', inventory: '背包 / 持有物', relationships: '与他人的关系', goals: '目标', fears: '恐惧', abilities: '能力', secrets: '秘密', current_state: '当前状态', status: '状态' }
const statusOptions = [['active', '在案'], ['dead', '已死亡'], ['archived', '归档']]

const groups: { title: string; fields: [keyof CharacterForm, string, boolean][] }[] = [
  { title: '基本信息', fields: [['name', '姓名', false], ['aliases', '别名', false], ['role', '身份 / 定位', false], ['age', '年龄', false]] },
  { title: '人物画像', fields: [['summary', '简介', true], ['appearance', '外貌', true], ['personality', '性格', true], ['identity', '身份描述', true]] },
  { title: '能力与世界观', fields: [['level', '等级 / 境界', false], ['abilities', '能力', true], ['inventory', '背包 / 持有物', true], ['current_state', '当前状态', true]] },
  { title: '动机与关系', fields: [['goals', '目标', true], ['fears', '恐惧', true], ['secrets', '秘密', true], ['relationships', '与他人的关系', true]] },
]

export function CharactersPage() {
  const { currentBook } = useAppStore()
  const [items, setItems] = useState<Character[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Character | null>(null)
  const [form, setForm] = useState<CharacterForm>(emptyForm)
  const [savedForm, setSavedForm] = useState<CharacterForm>(emptyForm)
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [removeBusy, setRemoveBusy] = useState(false)
  const [pendingSwitch, setPendingSwitch] = useState<Character | 'new' | null>(null)
  const [versions, setVersions] = useState<CharacterVersion[]>([])
  const [showVersions, setShowVersions] = useState(false)
  const [viewing, setViewing] = useState<CharacterVersion | null>(null)

  const dirty = JSON.stringify(form) !== JSON.stringify(savedForm)

  const load = async () => {
    if (!currentBook) return
    setLoading(true); setError('')
    try { setItems(await api<Character[]>(`/books/${currentBook.id}/characters`)) } catch (exc) { setError(String(exc)) } finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [currentBook?.id])

  const edit = (item: Character | null) => {
    setSelected(item)
    const next = item ? { ...emptyForm, ...item } : emptyForm
    setForm(next); setSavedForm(next); setError('')
  }
  const requestEdit = (item: Character | null) => {
    if (item ? item.id === selected?.id : !selected) return
    if (dirty) setPendingSwitch(item || 'new')
    else edit(item)
  }
  const set = (key: keyof CharacterForm, value: string) => setForm(prev => ({ ...prev, [key]: value }))

  const save = async () => {
    if (!currentBook || !form.name.trim()) { setError('请填写角色姓名'); return }
    setSaving(true); setError('')
    try {
      const item = selected
        ? await api<Character>(`/characters/${selected.id}`, { method: 'PUT', body: JSON.stringify(form) })
        : await api<Character>(`/books/${currentBook.id}/characters`, { method: 'POST', body: JSON.stringify(form) })
      setItems(selected ? items.map(x => x.id === item.id ? item : x) : [...items, item])
      edit(item); toast(selected ? `「${item.name}」已保存，旧版本已归档` : `角色「${item.name}」已创建`)
    } catch (exc) { setError(String(exc)); toast('保存角色失败', 'danger') } finally { setSaving(false) }
  }
  const remove = async () => {
    if (!selected) return
    setRemoveBusy(true)
    try {
      await api(`/characters/${selected.id}?confirm=true`, { method: 'DELETE' })
      setItems(items.filter(x => x.id !== selected.id)); edit(null); setRemoving(false)
      toast(`角色「${selected.name}」已删除`)
    } catch (exc) { setError(String(exc)) } finally { setRemoveBusy(false) }
  }
  const openVersions = async () => {
    if (!selected) return
    try { setVersions(await api<CharacterVersion[]>(`/characters/${selected.id}/versions`)); setViewing(null); setShowVersions(true) } catch (exc) { setError(String(exc)) }
  }

  const filtered = items.filter(x => `${x.name}${x.aliases}${x.role}`.includes(query))

  return <div className="p-6">
    <PageHeader title="角色卡" description="结构化记录角色状态。每次保存都会归档一个历史版本，可回溯。" actions={
      <Button variant="primary" onClick={() => requestEdit(null)}><UserPlus size={15} />新建角色</Button>} />
    <div className="mt-5 space-y-4">
      {error && <InlineError message={error} />}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <Panel title={`角色 · ${items.length}`} className="self-start" bodyClassName="max-h-[70vh] overflow-y-auto scrollbar-thin">
          <div className="relative mb-3">
            <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint" />
            <Input className="py-1.5 pl-7 text-meta" placeholder="搜索姓名、别名或身份" value={query} onChange={e => setQuery(e.target.value)} aria-label="搜索角色" />
          </div>
          {loading ? <Loading /> : filtered.length === 0 ? <Empty compact icon={<Users size={18} />} title={query ? '没有匹配的角色' : '暂无角色'} text={query ? '换个关键词试试。' : '先建立一个角色卡。'} /> : <div className="space-y-1">
            {filtered.map(item => <button key={item.id} onClick={() => requestEdit(item)} className={`w-full rounded-md border-l-2 px-3 py-2.5 text-left transition-colors duration-fast ${selected?.id === item.id ? 'border-accent-600 bg-accent-50' : 'border-transparent hover:bg-paper-sunken'}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium text-ink">{item.name}</span>
                <StatusBadge status={item.status} label={statusOptions.find(([key]) => key === item.status)?.[1] || item.status} />
              </div>
              <MetaText className="mt-0.5 truncate">{item.role || '未填写身份'}</MetaText>
            </button>)}
          </div>}
        </Panel>

        <Panel title={selected ? `编辑：${selected.name}` : '新建角色'} description={selected ? `最近更新 ${new Date(selected.updated_at).toLocaleString()}` : '填写姓名即可创建，其余慢慢补。'}
          action={selected && <div className="flex gap-1.5">
            <Button size="sm" variant="ghost" onClick={() => void openVersions()}><History size={13} />历史版本</Button>
            <Button size="sm" variant="danger" onClick={() => setRemoving(true)}>删除</Button>
          </div>}>
          <div className="space-y-6">
            {groups.map(group => <div key={group.title}>
              <SectionTitle>{group.title}</SectionTitle>
              <div className="mt-2.5 grid grid-cols-1 gap-4 md:grid-cols-2">
                {group.fields.map(([key, label, long]) => <Field key={key} label={label} className={long ? 'md:col-span-2' : ''}>
                  {long
                    ? <Textarea rows={2} value={form[key]} onChange={e => set(key, e.target.value)} />
                    : <Input value={form[key]} onChange={e => set(key, e.target.value)} />}
                </Field>)}
              </div>
            </div>)}
            <Field label="状态">
              <Select className="max-w-48" value={form.status} onChange={e => set('status', e.target.value)}>
                {statusOptions.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </Select>
            </Field>
            <div className="flex items-center gap-3 border-t border-line-soft pt-4">
              <Button variant="primary" disabled={saving} onClick={() => void save()}>{saving ? '保存中…' : selected ? '保存修改' : '创建角色'}</Button>
              {dirty && <Badge tone="warn">有未保存修改</Badge>}
              {!dirty && selected && <MetaText>所有修改已保存</MetaText>}
            </div>
          </div>
        </Panel>
      </div>
    </div>

    <ConfirmModal open={!!pendingSwitch} onClose={() => setPendingSwitch(null)} title="有未保存的角色修改"
      description="切换前未保存的修改会丢失。"
      confirmText="放弃修改并切换"
      onConfirm={() => { const target = pendingSwitch; setPendingSwitch(null); edit(target === 'new' ? null : target) }} />
    <ConfirmModal open={removing} onClose={() => setRemoving(false)} onConfirm={() => void remove()} danger busy={removeBusy}
      title={`删除角色「${selected?.name}」？`} confirmText="永久删除"
      description="角色卡及其历史版本都会被删除，已入库的关系图节点会随之消失。此操作不可撤销。" />

    <Modal open={showVersions} onClose={() => setShowVersions(false)} title={`「${selected?.name}」的历史版本`} description="每次保存或自动入库都会生成一个版本，这里只读查看，不会改动资料库。" width="max-w-3xl"
      footer={<Button variant="ghost" onClick={() => setShowVersions(false)}>关闭</Button>}>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-[200px_minmax(0,1fr)]">
        <div className="space-y-1.5">
          {versions.map(version => <button key={version.id} onClick={() => setViewing(version)} className={`w-full rounded-md border px-3 py-2 text-left text-body transition-colors ${viewing?.id === version.id ? 'border-accent-300 bg-accent-50' : 'border-line-soft hover:bg-paper-sunken'}`}>
            <div className="font-medium text-ink">v{version.version_number}</div>
            <MetaText>{new Date(version.created_at).toLocaleString()}</MetaText>
          </button>)}
          {versions.length === 0 && <MetaText>暂无历史版本</MetaText>}
        </div>
        <div className="min-h-40">
          {viewing ? <div>
            <MetaText>来源：{viewing.source} {viewing.change_reason && `· ${viewing.change_reason}`}</MetaText>
            <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
              {Object.entries(viewing.structured_json || {}).filter(([, value]) => value !== '' && value !== null && value !== undefined).map(([key, value]) => <div key={key} className="rounded-md bg-paper-sunken px-3 py-2">
                <MetaText>{fieldLabels[key] || key}</MetaText>
                <div className="mt-0.5 whitespace-pre-wrap break-words text-body text-ink-soft">{typeof value === 'string' ? value : JSON.stringify(value)}</div>
              </div>)}
            </div>
          </div> : <Empty compact title="选择左侧版本查看内容" />}
        </div>
      </div>
    </Modal>
  </div>
}
