import { useEffect, useState } from 'react'
import { PlugZap, Plus } from 'lucide-react'
import { api } from '../api/client'
import type { Provider } from '../types'
import { Badge, Button, Empty, Field, InlineError, InlineNotice, Input, Loading, MetaText, PageHeader, Panel, toast } from '../lib/ui'

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
      if (result.ok) { setMessage(result.message); toast('连接成功') } else { setMessage(''); setError(result.message); toast('连接失败', 'danger') }
    } catch (exc) { setError(exc instanceof Error ? exc.message : '测试连接失败') } finally { setTesting(false) }
  }

  if (loading) return <Loading text="正在读取模型设置…" />

  return <div className="space-y-4">
    {message && <InlineNotice tone="accent" message={message} />}
    {error && <InlineError message={error} />}
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <Panel title={`模型配置 · ${items.length}`} className="self-start" action={<Button size="sm" variant="subtle" onClick={() => edit(null)}><Plus size={13} />新建</Button>}>
        {items.length === 0 ? <Empty compact title="暂无配置" text="添加并保存 OpenAI Compatible 接口配置。" /> : <div className="space-y-1.5">
          {items.map(item => <button key={item.id} onClick={() => edit(item)} className={`w-full rounded-md border px-3 py-2.5 text-left transition-colors duration-fast ${selected?.id === item.id ? 'border-accent-300 bg-accent-50/60' : 'border-line-soft hover:bg-paper-sunken'}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-medium text-ink">{item.provider_name}</span>
              <Badge tone={item.enabled ? 'ok' : 'neutral'}>{item.enabled ? '启用' : '停用'}</Badge>
            </div>
            <MetaText className="mt-0.5">{item.model} · Key {item.has_api_key ? '已设置' : '未设置'}</MetaText>
          </button>)}
        </div>}
      </Panel>
      <Panel title={selected ? `编辑：${selected.provider_name}` : '新建配置'} description="支持 OpenAI Compatible 接口；保存配置不会自动调用模型。">
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
        </div>
      </Panel>
    </div>
  </div>
}

export function SettingsPage() {
  return <div className="p-6">
    <PageHeader title="LLM 配置" description="管理模型接口、密钥与连接测试。配置全局共用，正文与大纲保存不会触发模型调用。" />
    <div className="mt-5"><ModelSettings /></div>
  </div>
}
