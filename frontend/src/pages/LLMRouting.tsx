import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Provider } from '../types'
import { Button, InlineError, Loading, MetaText, Panel, Select, toast } from '../lib/ui'

type Cooldown = { until: string; reason: string } | null
type Route = {
  module: string; label: string; description: string; provider_id: number | null; use_default: boolean; revision: number
  effective_provider: { id: number; provider_name: string; model: string; cooldown?: Cooldown } | null; error: string | null
  fallbacks?: { position: number; provider_id: number | null; model: string | null; error: string | null; cooldown?: Cooldown }[]
}
const backupSelection = (row: Route) => Array.from({ length: 3 }, (_, index) => {
  const slot = row.fallbacks?.[index]
  return slot ? slot.provider_id === null ? 'missing' : String(slot.provider_id) : ''
})
const selection = (row: Route) => row.use_default ? '' : row.provider_id === null ? 'missing' : String(row.provider_id)

export function LLMRouting({ active, onDirty }: { active: boolean; onDirty: (dirty: boolean) => void }) {
  const [rows, setRows] = useState<Route[]>([])
  const [providers, setProviders] = useState<Provider[]>([])
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [backups, setBackups] = useState<Record<string, string[]>>({})
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const rowDirty = (row: Route) => draft[row.module] !== selection(row) || JSON.stringify(backups[row.module]) !== JSON.stringify(backupSelection(row))
  const dirty = rows.some(rowDirty)
  useEffect(() => onDirty(dirty || !!busy), [dirty, busy, onDirty])
  const load = async () => {
    setLoading(true); setError('')
    try {
      const [routes, models] = await Promise.all([api<Route[]>('/llm/routes'), api<Provider[]>('/llm/providers')])
      setRows(routes); setProviders(models); setDraft(Object.fromEntries(routes.map(row => [row.module, selection(row)]))); setBackups(Object.fromEntries(routes.map(row => [row.module, backupSelection(row)])))
    } catch (exc) { setError(String(exc)) }
    finally { setLoading(false) }
  }
  useEffect(() => { if (active && !dirty && !busy) void load() }, [active])
  const save = async (row: Route) => {
    setBusy(row.module); setError('')
    try {
      const saved = await api<Route>(`/llm/routes/${row.module}`, { method: 'PUT', body: JSON.stringify({
        provider_id: draft[row.module] ? Number(draft[row.module]) : null, base_revision: row.revision,
        ...(row.fallbacks ? { fallback_provider_ids: (backups[row.module] || []).filter(Boolean).map(Number) } : {}),
      }) })
      setRows(previous => previous.map(item => item.module === row.module ? saved : item))
      setDraft(previous => ({ ...previous, [row.module]: selection(saved) })); setBackups(previous => ({ ...previous, [row.module]: backupSelection(saved) }))
      toast(`${row.label}的模型分流已保存`)
    } catch (exc) { setError(String(exc)) }
    finally { setBusy('') }
  }
  return <Panel title="模块分流" description="每个模块独立选择主模型及最多 3 个有序备用。没有全局备用；不设置备用时，遇到错误就停止。新分流在启动或继续任务时生效。" action={
    <Button variant="ghost" disabled={loading || !!busy} onClick={() => { if (!dirty || window.confirm('放弃未保存的分流修改并重新读取？')) void load() }}>重新读取分流</Button>
  }>
    {error && <InlineError message={error} />}
    {loading ? <Loading text="正在读取模块分流…" /> : <div className="space-y-4">
      {rows.map(row => <div key={row.module} className="grid gap-3 rounded-md border border-line-soft p-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
        <div><div className="font-medium">{row.label}</div><MetaText className="mt-1">{row.description}</MetaText></div>
        <div>
          <MetaText className="mb-1">主模型</MetaText>
          <Select aria-label={`${row.label}使用的模型`} className="w-full" value={draft[row.module] ?? selection(row)} disabled={!!busy} onChange={event => setDraft(previous => ({ ...previous, [row.module]: event.target.value }))}>
            <option value="">{row.module === 'chapter_writing' ? '默认 Gemini 配置' : '跟随默认模型'}</option>
            {!row.use_default && row.provider_id === null && <option value="missing" disabled>指定配置已删除，请重新选择</option>}
            {row.provider_id !== null && !providers.some(p => p.id === row.provider_id) && <option value={String(row.provider_id)} disabled>指定配置不可用</option>}
            {providers.map(p => <option key={p.id} value={String(p.id)} disabled={!p.has_api_key}>{p.provider_name} · {p.model}{p.enabled ? '（默认）' : ''}{!p.has_api_key ? '（未设置密钥）' : ''}</option>)}
          </Select>
          <MetaText className="mt-2">当前生效：{row.effective_provider ? `${row.effective_provider.provider_name} · ${row.effective_provider.model}` : '不可用'}</MetaText>
          {row.effective_provider?.cooldown && <MetaText className="mt-1 text-warn-strong">主模型冷却至 {new Date(row.effective_provider.cooldown.until).toLocaleString()}：{row.effective_provider.cooldown.reason}</MetaText>}
          <div className="mt-3 space-y-2">
            {[0, 1, 2].map(index => <label key={index} className="flex items-center gap-2">
              <span className="shrink-0 text-meta text-ink-soft">备用 {index + 1}</span>
              <Select aria-label={`${row.label}备用模型 ${index + 1}`} value={backups[row.module]?.[index] || ''}
                disabled={!!busy || !row.fallbacks || (index > 0 && !backups[row.module]?.[index - 1])}
                onChange={event => setBackups(previous => {
                  const next = [...(previous[row.module] || ['', '', ''])]
                  next[index] = event.target.value
                  if (!event.target.value) next.fill('', index + 1)
                  return { ...previous, [row.module]: next }
                })}>
                <option value="">不设置{index === 0 ? '（出错即停止）' : '（到此结束）'}</option>
                {backups[row.module]?.[index] === 'missing' && <option value="missing" disabled>备用配置已删除，请重新选择</option>}
                {providers.map(p => <option key={p.id} value={String(p.id)}
                  disabled={!p.has_api_key || String(p.id) === (draft[row.module] || String(row.effective_provider?.id)) || (backups[row.module] || []).some((id, i) => i !== index && id === String(p.id))}>
                  {p.provider_name} · {p.model}{!p.has_api_key ? '（未设置密钥）' : ''}
                </option>)}
              </Select>
            </label>)}
          </div>
          {!row.fallbacks && <MetaText>等待后端更新备用分流接口。</MetaText>}
          {row.fallbacks?.map(slot => slot.error || slot.cooldown ? <MetaText key={slot.position} className="mt-1 text-warn-strong">备用 {slot.position}：{slot.error || `冷却至 ${new Date(slot.cooldown!.until).toLocaleString()}`}</MetaText> : null)}
          {row.error && <div className="mt-1 text-meta text-warn-strong">{row.error}</div>}
          {rowDirty(row) && <MetaText>有未保存修改</MetaText>}
        </div>
        <Button variant="secondary" className="self-start" aria-label={`保存${row.label}分流`} disabled={!!busy || !rowDirty(row) || draft[row.module] === 'missing' || backups[row.module]?.includes('missing')} onClick={() => void save(row)}>{busy === row.module ? '保存中…' : '保存'}</Button>
      </div>)}
      <div className="rounded-md bg-paper-sunken p-4 text-meta leading-6 text-ink-soft">
        <div>连接测试、读取可用模型：直接使用“模型列表”中正在编辑且已保存的配置，无需分流。</div>
        <div>正文生成用于正文页的持续生成工作流，默认选择 Gemini；正文、大纲和资料的手动保存不调用模型。</div>
        <div>梗概和速查遇到限流、接口报错、连接超时或返回格式错误时，按本模块备用顺序尝试。正文仅在限流或额度受限时使用已设置的备用；留空即不切换。每个模型最多一次，全部不可用就停止。</div>
        <div>冷却优先采用服务提供的恢复时间，未提供时为 5 分钟；冷却期间仅尝试本模块已选备用，不会等待数小时阻塞页面。</div>
        <div>正文需要固定 Gemini 时，保留正文生成的备用为空。格式错误、普通 400、超时和内容冲突不会换模型。</div>
        <div>备用使用各自的地址、密钥和输出参数，提示词与资料保持一致。配置删除或清除密钥时需重新选择；切换分页保留未保存修改。</div>
      </div>
    </div>}
  </Panel>
}
