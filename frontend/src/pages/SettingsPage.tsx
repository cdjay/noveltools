import { useEffect, useState } from 'react'
import { Download, PlugZap, Plus } from 'lucide-react'
import { api, API_BASE } from '../api/client'
import { useAppStore } from '../stores/useAppStore'
import type { Book, Provider } from '../types'
import { Badge, Button, DangerZoneNote, Empty, Field, InlineError, InlineNotice, Input, Loading, MetaText, PageHeader, Panel, Select, Tabs, Textarea, toast } from '../lib/ui'

type ProviderForm = {
  provider_name: string
  provider_type: 'openai_compatible'
  base_url: string
  api_key: string
  model: string
  temperature: number
  max_tokens: number
  timeout: number
  enabled: boolean
}

const defaultForm: ProviderForm = {
  provider_name: 'OpenAI Compatible', provider_type: 'openai_compatible',
  base_url: 'https://api.openai.com/v1', api_key: '', model: 'gpt-4o-mini',
  temperature: 0.2, max_tokens: 3000, timeout: 60, enabled: true,
}

const formFromProvider = (item: Provider): ProviderForm => ({
  provider_name: item.provider_name, provider_type: 'openai_compatible',
  base_url: item.base_url, api_key: '', model: item.model,
  temperature: item.temperature, max_tokens: item.max_tokens,
  timeout: item.timeout, enabled: item.enabled,
})

function ModelSettings() {
  const [items, setItems] = useState<Provider[]>([])
  const [selected, setSelected] = useState<Provider | null>(null)
  const [form, setForm] = useState<ProviderForm>(defaultForm)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

  const load = async () => {
    setLoading(true); setError('')
    try { setItems(await api<Provider[]>('/llm/providers')) }
    catch (exc) { setError(exc instanceof Error ? exc.message : '读取模型配置失败') }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])

  const edit = (item: Provider | null) => {
    setSelected(item); setForm(item ? formFromProvider(item) : { ...defaultForm })
    setMessage(''); setError('')
  }
  const save = async () => {
    setError(''); setSaving(true)
    try {
      const item = selected
        ? await api<Provider>(`/llm/providers/${selected.id}`, { method: 'PUT', body: JSON.stringify(form) })
        : await api<Provider>('/llm/providers', { method: 'POST', body: JSON.stringify(form) })
      setItems(previous => selected ? previous.map(value => value.id === item.id ? item : value) : [item, ...previous])
      edit(item); toast('配置已保存；API Key 只保存、不回显。')
    } catch (exc) { setError(exc instanceof Error ? exc.message : '保存配置失败') } finally { setSaving(false) }
  }
  const test = async () => {
    if (!selected) return
    setError(''); setTesting(true)
    try {
      const result = await api<{ ok: boolean; message: string }>(`/llm/providers/${selected.id}/test`, { method: 'POST' })
      setMessage(result.message); toast('连接测试完成', 'accent')
    } catch (exc) { setError(exc instanceof Error ? exc.message : '测试连接失败') } finally { setTesting(false) }
  }

  if (loading) return <Loading text="正在读取模型设置…" />

  return <div className="space-y-4">
    {message && <InlineNotice tone="accent" message={message} />}
    {error && <InlineError message={error} />}
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <Panel title={`模型配置 · ${items.length}`} className="self-start" action={<Button size="sm" variant="subtle" onClick={() => edit(null)}><Plus size={13} />新建</Button>}>
        {items.length === 0 ? <Empty compact title="暂无配置" text="添加一个 OpenAI Compatible 配置后即可运行分析。" /> : <div className="space-y-1.5">
          {items.map(item => <button key={item.id} onClick={() => edit(item)} className={`w-full rounded-md border px-3 py-2.5 text-left transition-colors duration-fast ${selected?.id === item.id ? 'border-accent-300 bg-accent-50/60' : 'border-line-soft hover:bg-paper-sunken'}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-medium text-ink">{item.provider_name}</span>
              <Badge tone={item.enabled ? 'ok' : 'neutral'}>{item.enabled ? '启用' : '停用'}</Badge>
            </div>
            <MetaText className="mt-0.5">{item.model} · Key {item.has_api_key ? '已设置' : '未设置'}</MetaText>
          </button>)}
        </div>}
      </Panel>
      <Panel title={selected ? `编辑：${selected.provider_name}` : '新建配置'} description="仅支持 OpenAI Compatible 接口，用于小说资料分析。">
        <div className="max-w-2xl space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Field label="名称"><Input value={form.provider_name} onChange={event => setForm({ ...form, provider_name: event.target.value })} /></Field>
            <Field label="模型名"><Input value={form.model} onChange={event => setForm({ ...form, model: event.target.value })} /></Field>
          </div>
          <Field label="Base URL"><Input value={form.base_url} onChange={event => setForm({ ...form, base_url: event.target.value })} /></Field>
          <Field label="API Key" hint={selected?.has_api_key ? '已设置，留空表示保留原值。' : '密钥只写入本地数据库，不会在前端回显。'}>
            <Input type="password" value={form.api_key} onChange={event => setForm({ ...form, api_key: event.target.value })} placeholder={selected?.has_api_key ? '留空保留原值' : 'sk-…'} />
          </Field>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <Field label="Temperature"><Input type="number" step="0.1" min="0" max="2" value={form.temperature} onChange={event => setForm({ ...form, temperature: Number(event.target.value) })} /></Field>
            <Field label="最大 Tokens"><Input type="number" min={100} value={form.max_tokens} onChange={event => setForm({ ...form, max_tokens: Number(event.target.value) })} /></Field>
            <Field label="超时（秒）"><Input type="number" min={5} value={form.timeout} onChange={event => setForm({ ...form, timeout: Number(event.target.value) })} /></Field>
          </div>
          <label className="flex items-center gap-2 text-body text-ink-soft">
            <input type="checkbox" className="accent-accent-600" checked={form.enabled} onChange={event => setForm({ ...form, enabled: event.target.checked })} />启用此配置
          </label>
          <div className="flex gap-2 border-t border-line-soft pt-4">
            <Button variant="primary" disabled={saving} onClick={() => void save()}>{saving ? '保存中…' : '保存配置'}</Button>
            <Button variant="secondary" disabled={!selected || testing} onClick={() => void test()}><PlugZap size={14} />{testing ? '测试中…' : '测试连接'}</Button>
          </div>
          <DangerZoneNote>安全边界：分析前会保存原始响应；分析失败不会覆盖正文或资料库；无冲突变更自动入库，冲突项只记录日志。未配置可用 API Key 时，分析只产生本地保守的占位结果并明确提示。</DangerZoneNote>
        </div>
      </Panel>
    </div>
  </div>
}

function BookSettings() {
  const { currentBook, setCurrentBook } = useAppStore()
  const [form, setForm] = useState({ title: '', author: '', genre: '', status: 'draft', description: '' })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (currentBook) setForm({ title: currentBook.title, author: currentBook.author, genre: currentBook.genre, status: currentBook.status, description: currentBook.description })
  }, [currentBook?.id])
  if (!currentBook) return null
  const save = async () => {
    if (!form.title.trim()) { setError('请填写书名'); return }
    setSaving(true); setError('')
    try {
      const saved = await api<Book>(`/books/${currentBook.id}`, { method: 'PUT', body: JSON.stringify(form) })
      setCurrentBook({ ...currentBook, ...saved }); toast('书籍信息已更新')
    } catch (exc) { setError(String(exc)) } finally { setSaving(false) }
  }
  return <div className="space-y-4">
    {error && <InlineError message={error} />}
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Panel title="书籍信息" description="只影响展示与检索，不影响任何正文与资料数据。">
        <div className="space-y-4">
          <Field label="书名"><Input value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="作者"><Input value={form.author} onChange={e => setForm({ ...form, author: e.target.value })} /></Field>
            <Field label="类型"><Input value={form.genre} onChange={e => setForm({ ...form, genre: e.target.value })} /></Field>
          </div>
          <Field label="状态">
            <Select value={form.status} onChange={e => setForm({ ...form, status: e.target.value })}>
              <option value="draft">草稿</option><option value="ongoing">连载中</option><option value="completed">已完成</option>
            </Select>
          </Field>
          <Field label="简介"><Textarea rows={4} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} /></Field>
          <Button variant="primary" disabled={saving} onClick={() => void save()}>{saving ? '保存中…' : '保存书籍信息'}</Button>
        </div>
      </Panel>
      <Panel title="导出" description="导出内容实时从数据库生成，可作为迁移或备份。">
        <div className="space-y-3">
          <a href={`${API_BASE}/books/${currentBook.id}/export.json`} download className="flex items-center justify-between rounded-md border border-line bg-paper px-4 py-3 transition-colors hover:border-accent-300">
            <div>
              <div className="flex items-center gap-2 text-body font-medium text-ink"><Download size={15} />完整数据 JSON</div>
              <MetaText className="mt-0.5">书籍信息、全部章节与版本、角色卡、知识项。</MetaText>
            </div>
            <Badge tone="accent">备份</Badge>
          </a>
          <a href={`${API_BASE}/books/${currentBook.id}/export.txt`} download className="flex items-center justify-between rounded-md border border-line bg-paper px-4 py-3 transition-colors hover:border-accent-300">
            <div>
              <div className="flex items-center gap-2 text-body font-medium text-ink"><Download size={15} />正文 TXT</div>
              <MetaText className="mt-0.5">按章节顺序拼接的当前正文，适合交稿或转移。</MetaText>
            </div>
            <Badge tone="accent">正文</Badge>
          </a>
          <DangerZoneNote>删除整本书的入口在书架页的书籍卡片上，删除前建议先导出 JSON 备份。</DangerZoneNote>
        </div>
      </Panel>
    </div>
  </div>
}

export function SettingsPage() {
  const [tab, setTab] = useState<'model' | 'book'>('model')
  return <div className="p-6">
    <PageHeader title="设置" description="管理分析模型与当前书籍的信息、导出。" />
    <Tabs className="mt-5" value={tab} onChange={setTab} items={[['model', '分析模型'], ['book', '书籍与导出']]} />
    <div className="mt-4">{tab === 'model' ? <ModelSettings /> : <BookSettings />}</div>
  </div>
}
