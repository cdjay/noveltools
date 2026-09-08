import { useEffect, useState } from 'react'
import { Copy, PlugZap, Plus } from 'lucide-react'
import { api } from '../api/client'
import type { Provider } from '../types'
import { LLMRouting } from './LLMRouting'
import { useUnsavedChanges } from '../lib/useUnsavedChanges'
import { Badge, Button, Empty, Field, InlineError, InlineNotice, Input, Loading, MetaText, PageHeader, Panel, Tabs, toast } from '../lib/ui'

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
  provider_name: '', provider_type: 'openai_compatible',
  base_url: '', api_key: '', model: '',
  temperature: 0.2, max_tokens: 3000, timeout: 1800, enabled: true,
}

const formFromProvider = (item: Provider): ProviderForm => ({
  provider_name: item.provider_name, provider_type: 'openai_compatible',
  base_url: item.base_url, api_key: '', model: item.model,
  temperature: item.temperature, max_tokens: item.max_tokens,
  timeout: item.timeout, enabled: item.enabled,
})

const initialProvider = (items: Provider[]) => items.find(item => item.enabled) || items[0] || null

function ModelSettings({ onDirty }: { onDirty: (dirty: boolean) => void }) {
  const [items, setItems] = useState<Provider[]>([])
  const [selected, setSelected] = useState<Provider | null>(null)
  const [form, setForm] = useState<ProviderForm>(defaultForm)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [models, setModels] = useState<string[]>([])
  const [fetchingModels, setFetchingModels] = useState(false)
  const dirty = JSON.stringify(form) !== JSON.stringify(selected ? formFromProvider(selected) : defaultForm)
  useEffect(() => onDirty(dirty || saving || testing || fetchingModels), [dirty, saving, testing, fetchingModels, onDirty])
  const fetchModels = async () => {
    if (!selected || dirty || saving) return
    setFetchingModels(true); setError('')
    try { setModels(await api<string[]>(`/llm/providers/${selected.id}/models`)) }
    catch (exc) { setError(String(exc)) }
    finally { setFetchingModels(false) }
  }
  const remove = async (keyOnly: boolean) => {
    if (!selected || !window.confirm(keyOnly ? '清除已保存的 API Key 并停用此配置？' : `删除「${selected.provider_name}」配置？`)) return
    setSaving(true); setError('')
    try {
      await api(`/llm/providers/${selected.id}${keyOnly ? '/key' : ''}`, { method: 'DELETE' })
      const next = await api<Provider[]>('/llm/providers')
      setItems(next); edit(keyOnly ? next.find(item => item.id === selected.id) || initialProvider(next) : initialProvider(next))
      toast(keyOnly ? '密钥已清除，配置已停用' : '配置已删除')
    } catch (exc) { setError(String(exc)) }
    finally { setSaving(false) }
  }

  const load = async () => {
    setLoading(true); setError('')
    try {
      const next = await api<Provider[]>('/llm/providers')
      setItems(next); edit(initialProvider(next))
    }
    catch (exc) { setError(exc instanceof Error ? exc.message : '读取模型配置失败') }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])

  const edit = (item: Provider | null) => {
    setSelected(item); setForm(item ? formFromProvider(item) : { ...defaultForm })
    setMessage(''); setError(''); setModels([])
  }
  const choose = (item: Provider | null) => {
    if (saving || testing || fetchingModels) return
    if (dirty && !window.confirm('放弃未保存的配置修改？')) return
    edit(item)
  }
  const copy = async () => {
    if (!selected || dirty || saving || testing || fetchingModels) return
    setSaving(true); setError('')
    try {
      const item = await api<Provider>(`/llm/providers/${selected.id}/copy`, { method: 'POST' })
      setItems(previous => [item, ...previous]); edit(item)
      toast('副本已创建，地址和密钥已沿用；修改模型名后保存即可。')
    } catch (exc) { setError(String(exc)) }
    finally { setSaving(false) }
  }
  const save = async () => {
    if (!form.provider_name.trim() || !form.base_url.trim() || !form.model.trim()) {
      setError('请填写名称、Base URL 和模型名。'); return
    }
    setError(''); setSaving(true)
    try {
      const item = selected
        ? await api<Provider>(`/llm/providers/${selected.id}`, { method: 'PUT', body: JSON.stringify(form) })
        : await api<Provider>('/llm/providers', { method: 'POST', body: JSON.stringify(form) })
      setItems(await api<Provider[]>('/llm/providers'))
      edit(item); toast('配置已保存；API Key 只保存、不回显。')
    } catch (exc) { setError(exc instanceof Error ? exc.message : '保存配置失败') } finally { setSaving(false) }
  }
  const test = async () => {
    if (!selected || dirty || saving) return
    setError(''); setTesting(true)
    try {
      const result = await api<{ ok: boolean; message: string }>(`/llm/providers/${selected.id}/test`, { method: 'POST' })
      if (result.ok) { setMessage(result.message); toast('连接成功') } else { setMessage(''); setError(result.message); toast('连接失败', 'danger') }
    } catch (exc) { setError(exc instanceof Error ? exc.message : '测试连接失败') } finally { setTesting(false) }
  }

  if (loading) return <Loading text="正在读取模型设置…" />

  return <div className="space-y-4">
    <InlineNotice tone="accent" message={`默认生成模型：${items.find(item => item.enabled && item.has_api_key)?.model || '未设置可用默认模型'}。未单独分流的模块使用默认模型；其他配置可在“模块分流”中指定。`} />
    {message && <InlineNotice tone="accent" message={message} />}
    {error && <InlineError message={error} />}
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <Panel title={`模型配置 · ${items.length}`} className="self-start" action={<Button size="sm" variant="subtle" disabled={saving || testing || fetchingModels} onClick={() => choose(null)}><Plus size={13} />新建</Button>}>
        {items.length === 0 ? <Empty compact title="暂无配置" text="添加并保存 OpenAI Compatible 接口配置。" /> : <div className="space-y-1.5">
          {items.map(item => <button key={item.id} disabled={saving || testing || fetchingModels} onClick={() => choose(item)} className={`w-full rounded-md border px-3 py-2.5 text-left transition-colors duration-fast ${selected?.id === item.id ? 'border-accent-300 bg-accent-50/60' : 'border-line-soft hover:bg-paper-sunken'}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-medium text-ink">{item.provider_name}</span>
              <Badge tone={item.enabled ? 'ok' : 'neutral'}>{item.enabled ? '默认' : '可分流'}</Badge>
            </div>
            <MetaText className="mt-0.5">{item.model} · Key {item.has_api_key ? '已设置' : '未设置'}</MetaText>
          </button>)}
        </div>}
      </Panel>
      <Panel title={selected ? `编辑：${selected.provider_name}` : '新建配置'} description="支持 OpenAI Compatible 接口；保存配置不会自动调用模型。">
        <fieldset disabled={saving || testing || fetchingModels} className="min-w-0 max-w-2xl space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Field label="名称"><Input value={form.provider_name} onChange={event => setForm({ ...form, provider_name: event.target.value })} /></Field>
            <Field label="模型名" hint="按服务模型列表填写完整 ID，注意大小写。"><Input list="provider-model-ids" value={form.model} onChange={event => setForm({ ...form, model: event.target.value })} /></Field>
          </div>
          <datalist id="provider-model-ids">{models.map(model => <option key={model} value={model} />)}</datalist>
          <div className="flex items-center gap-2"><Button size="sm" variant="secondary" disabled={!selected || fetchingModels || dirty || saving} onClick={() => void fetchModels()}>{fetchingModels ? '读取中…' : '读取可用模型'}</Button><MetaText>{models.length ? `已读取 ${models.length} 个模型，请在模型名中选择。` : '读取使用已保存的地址与密钥。'}</MetaText></div>
          <Field label="Base URL"><Input value={form.base_url} onChange={event => setForm({ ...form, base_url: event.target.value })} /></Field>
          <Field label="API Key" hint={selected?.has_api_key ? '已设置，留空表示保留原值。' : '密钥只写入本地数据库，不会在前端回显。'}>
            <Input type="password" value={form.api_key} onChange={event => setForm({ ...form, api_key: event.target.value })} placeholder={selected?.has_api_key ? '留空保留原值' : 'sk-…'} />
          </Field>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <Field label="Temperature"><Input type="number" step="0.1" min="0" max="2" value={form.temperature} onChange={event => setForm({ ...form, temperature: Number(event.target.value) })} /></Field>
            <Field label="最大输出 Tokens" hint="仅限制模型回复长度，不是输入上下文容量。需低于该模型的输出上限。"><Input type="number" min={100} value={form.max_tokens} onChange={event => setForm({ ...form, max_tokens: Number(event.target.value) })} /></Field>
            <Field label="生成等待（分钟）" hint={`保存值 ${form.timeout} 秒；默认 30 分钟，可继续调大。`}><Input type="number" min={0.1} step={0.1} value={Number((form.timeout / 60).toFixed(3))} onChange={event => setForm({ ...form, timeout: Math.round(Number(event.target.value) * 60) })} /></Field>
          </div>
          <label className="flex items-center gap-2 text-body text-ink-soft">
            <input type="checkbox" className="accent-accent-600" checked={form.enabled} onChange={event => setForm({ ...form, enabled: event.target.checked })} />设为默认模型
          </label>
          <p className="text-meta text-ink-soft">最大输出 Tokens 控制生成结果长度，不是模型输入容量。连接测试只发送短句、最多输出 10 Tokens；测试成功不代表长文本生成或持续调用一定可用。</p>
          {dirty && <MetaText>配置有未保存修改，请先保存，再测试连接或读取模型。</MetaText>}
          {selected && !selected.enabled && <MetaText>此配置不是默认模型，可在“模块分流”中单独指定。连接测试不会改变默认或分流选择。</MetaText>}
          <div className="flex flex-wrap gap-2 border-t border-line-soft pt-4">
            <Button variant="primary" disabled={saving} onClick={() => void save()}>{saving ? '保存中…' : '保存配置'}</Button>
            {selected && <Button variant="secondary" disabled={dirty || saving} title={dirty ? '请先保存修改，再复制已保存配置' : '创建独立副本，沿用地址、密钥和模型参数'} onClick={() => void copy()}><Copy size={14} />复制配置</Button>}
            <Button variant="secondary" disabled={!selected || testing || dirty || saving} onClick={() => void test()}><PlugZap size={14} />{testing ? '测试中…' : '测试连接'}</Button>
            {selected && <><Button variant="ghost" disabled={saving || !selected.has_api_key} onClick={() => void remove(true)}>清除密钥</Button><Button variant="danger" disabled={saving} onClick={() => void remove(false)}>删除配置</Button></>}
          </div>
        </fieldset>
      </Panel>
    </div>
  </div>
}

export function SettingsPage() {
  const [tab, setTab] = useState<'models' | 'routing'>('models')
  const [modelDirty, setModelDirty] = useState(false)
  const [routingDirty, setRoutingDirty] = useState(false)
  useUnsavedChanges(modelDirty || routingDirty)
  return <div className="p-6">
    <PageHeader title="LLM 配置" description="管理模型连接，并为正文生成、章节梗概、批量梗概和当前状态速查分别指定模型。" />
    <Tabs className="mt-5" value={tab} onChange={setTab} items={[['models', '模型列表'], ['routing', '模块分流']]} />
    <div className="mt-5" hidden={tab !== 'models'}><ModelSettings onDirty={setModelDirty} /></div>
    <div className="mt-5" hidden={tab !== 'routing'}><LLMRouting active={tab === 'routing'} onDirty={setRoutingDirty} /></div>
  </div>
}
