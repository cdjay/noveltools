import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Provider } from '../types'
import { Button, InlineError, Loading, MetaText, Panel, Select, toast } from '../lib/ui'

type Route = {
  module: string; label: string; description: string; provider_id: number | null; use_default: boolean; revision: number
  effective_provider: { id: number; provider_name: string; model: string } | null; error: string | null
}
const selection = (row: Route) => row.use_default ? '' : row.provider_id === null ? 'missing' : String(row.provider_id)

export function LLMRouting({ active, onDirty }: { active: boolean; onDirty: (dirty: boolean) => void }) {
  const [rows, setRows] = useState<Route[]>([])
  const [providers, setProviders] = useState<Provider[]>([])
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const dirty = rows.some(row => draft[row.module] !== selection(row))
  useEffect(() => onDirty(dirty || !!busy), [dirty, busy, onDirty])
  const load = async () => {
    setLoading(true); setError('')
    try {
      const [routes, models] = await Promise.all([api<Route[]>('/llm/routes'), api<Provider[]>('/llm/providers')])
      setRows(routes); setProviders(models); setDraft(Object.fromEntries(routes.map(row => [row.module, selection(row)])))
    } catch (exc) { setError(String(exc)) }
    finally { setLoading(false) }
  }
  useEffect(() => { if (active && !dirty && !busy) void load() }, [active])
  const save = async (row: Route) => {
    setBusy(row.module); setError('')
    try {
      const saved = await api<Route>(`/llm/routes/${row.module}`, { method: 'PUT', body: JSON.stringify({
        provider_id: draft[row.module] ? Number(draft[row.module]) : null, base_revision: row.revision,
      }) })
      setRows(previous => previous.map(item => item.module === row.module ? saved : item))
      setDraft(previous => ({ ...previous, [row.module]: selection(saved) }))
      toast(`${row.label}的模型分流已保存`)
    } catch (exc) { setError(String(exc)) }
    finally { setBusy('') }
  }
  return <Panel title="模块分流" description="每个模块可使用默认模型，也可指定已保存的其他模型配置。更改用于下一次任务，不切换正在运行的请求。" action={
    <Button variant="ghost" disabled={loading || !!busy} onClick={() => { if (!dirty || window.confirm('放弃未保存的分流修改并重新读取？')) void load() }}>重新读取分流</Button>
  }>
    {error && <InlineError message={error} />}
    {loading ? <Loading text="正在读取模块分流…" /> : <div className="space-y-4">
      {rows.map(row => <div key={row.module} className="grid gap-3 rounded-md border border-line-soft p-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
        <div><div className="font-medium">{row.label}</div><MetaText className="mt-1">{row.description}</MetaText></div>
        <div>
          <Select aria-label={`${row.label}使用的模型`} className="w-full" value={draft[row.module] ?? selection(row)} disabled={!!busy} onChange={event => setDraft(previous => ({ ...previous, [row.module]: event.target.value }))}>
            <option value="">跟随默认模型</option>
            {!row.use_default && row.provider_id === null && <option value="missing" disabled>指定配置已删除，请重新选择</option>}
            {row.provider_id !== null && !providers.some(p => p.id === row.provider_id) && <option value={String(row.provider_id)} disabled>指定配置不可用</option>}
            {providers.map(p => <option key={p.id} value={String(p.id)} disabled={!p.has_api_key}>{p.provider_name} · {p.model}{p.enabled ? '（默认）' : ''}{!p.has_api_key ? '（未设置密钥）' : ''}</option>)}
          </Select>
          <MetaText className="mt-2">当前生效：{row.effective_provider ? `${row.effective_provider.provider_name} · ${row.effective_provider.model}` : '不可用'}</MetaText>
          {row.error && <div className="mt-1 text-meta text-warn-strong">{row.error}</div>}
          {draft[row.module] !== selection(row) && <MetaText>有未保存修改</MetaText>}
        </div>
        <Button variant="secondary" className="self-start" aria-label={`保存${row.label}分流`} disabled={!!busy || draft[row.module] === selection(row) || draft[row.module] === 'missing'} onClick={() => void save(row)}>{busy === row.module ? '保存中…' : '保存'}</Button>
      </div>)}
      <div className="rounded-md bg-paper-sunken p-4 text-meta leading-6 text-ink-soft">
        <div>连接测试、读取可用模型：直接使用“模型列表”中正在编辑且已保存的配置，无需分流。</div>
        <div>正文自动续写尚未接入，开发后会加入此列表。正文、大纲和资料保存不调用模型。</div>
        <div>指定配置被删除或清除密钥后，该模块会提示重新配置，不会自动改用其他模型。切换分页保留未保存修改。</div>
      </div>
    </div>}
  </Panel>
}
