import { useEffect, useRef, useState } from 'react'
import { History, RefreshCw, Save, Settings2, Sparkles } from 'lucide-react'
import { api, API_BASE } from '../api/client'
import { useAppStore } from '../stores/useAppStore'
import { useUiPrefs } from '../stores/useUiPrefs'
import { useUnsavedChanges } from '../lib/useUnsavedChanges'
import { CopyButton } from '../lib/CopyButton'
import { FormatSwitch, TextContent, TextEditor } from '../lib/TextContent'
import { Badge, Button, ConfirmModal, Empty, InlineError, Loading, MetaText, Modal, PageHeader, Panel, Textarea, toast } from '../lib/ui'

type StateVersion = { id: number; book_id: number; version_number: number; content: string; source: string; note: string; model: string; prompt: string; created_at: string; source_manifest: { body_chapter_ids?: number[]; chapters?: { chapter_number: number; title: string }[] } }
type CurrentState = { book_id: number; version: StateVersion | null; is_stale: boolean; generating: boolean }
type StateSettings = { prompt: string; revision: number; default_prompt: string }
type SourceContext = { outline?: { version_number: number; characters: number; is_empty: boolean } | null; body_chapters: { chapter_id: number; chapter_number: number; title: string }[]; chapter_count: number; summary_count: number; missing_summary_chapter_ids: number[]; stale_summary_chapter_ids: number[]; input_characters: number; model: string | null; max_output_tokens?: number | null; timeout_seconds?: number | null; source_error?: string }

export function CurrentStatePage() {
  const bookId = useAppStore(state => state.currentBook?.id)
  const { setTextFormat } = useUiPrefs()
  const [current, setCurrent] = useState<CurrentState | null>(null)
  const [versions, setVersions] = useState<StateVersion[]>([])
  const [context, setContext] = useState<SourceContext | null>(null)
  const [settings, setSettings] = useState<StateSettings | null>(null)
  const [content, setContent] = useState('')
  const [prompt, setPrompt] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [preview, setPreview] = useState<StateVersion | null>(null)
  const [restoring, setRestoring] = useState<StateVersion | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  const [settingsError, setSettingsError] = useState('')
  const alive = useRef(false)
  const loadSequence = useRef(0)
  const base = `/books/${bookId}/current-state`
  const dirty = !!current && content !== (current.version?.content || '')
  const promptDirty = !!settings && prompt !== settings.prompt
  useUnsavedChanges(dirty || promptDirty || generating)

  const load = async () => {
    const sequence = ++loadSequence.current
    setLoading(true); setError('')
    try {
      const [state, history, config, sources] = await Promise.all([
        api<CurrentState>(base), api<StateVersion[]>(`${base}/versions`),
        api<StateSettings>(`${base}/settings`), api<SourceContext>(`${base}/context`),
      ])
      if (!alive.current || sequence !== loadSequence.current) return
      setCurrent(state); setContent(state.version?.content || ''); setVersions(history)
      setSettings(config); setPrompt(config.prompt); setContext(sources)
      if (sources.source_error) setError(sources.source_error)
    } catch (exc) { if (alive.current && sequence === loadSequence.current) setError(String(exc)) }
    finally { if (alive.current && sequence === loadSequence.current) setLoading(false) }
  }
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; loadSequence.current++ } }, [bookId])
  // Reopening a page during a request in another window must not strand the busy flag.
  useEffect(() => {
    if (!current?.generating || generating) return
    let stopped = false
    const timer = window.setInterval(() => {
      void api<CurrentState>(base).then(state => {
        if (stopped) return
        if (!state.generating) {
          if (!dirty) { setCurrent(state); setContent(state.version?.content || '') }
          else { setCurrent(previous => previous ? { ...previous, generating: false } : previous); setError('其他窗口的生成已结束，当前草稿保留；请重新读取后核对。') }
          void api<StateVersion[]>(`${base}/versions`).then(history => { if (!stopped) setVersions(history) }).catch(() => undefined)
        }
      }).catch(() => undefined)
    }, 2000)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [base, current?.generating, generating, dirty])

  const save = async (source?: StateVersion) => {
    if (!current || busy || generating) return
    setBusy(true); setError('')
    try {
      const version = await api<StateVersion>(source ? `${base}/versions/${source.id}/restore` : `${base}/versions`, {
        method: 'POST', body: JSON.stringify({ content, base_version_id: current.version?.id ?? null }),
      })
      if (!alive.current) return
      setContent(version.content); setCurrent({ ...current, version }); setVersions(previous => [version, ...previous]); setRestoring(null)
      const fresh = await api<CurrentState>(base)
      if (alive.current && fresh.version?.id === version.id) setCurrent(fresh)
      toast(source ? '旧稿已恢复为新版本' : '状态速查已保存')
    } catch (exc) { if (alive.current) setError(String(exc)) }
    finally { if (alive.current) setBusy(false) }
  }
  const generate = async () => {
    if (!current || dirty || promptDirty || busy || generating) return
    setGenerating(true); setError('')
    try {
      const version = await api<StateVersion>(`${base}/generate`, { method: 'POST', body: JSON.stringify({ base_version_id: current.version?.id ?? null }) })
      if (!alive.current) return
      setContent(version.content); setCurrent({ ...current, version, is_stale: false, generating: false })
      setVersions(previous => [version, ...previous]); setTextFormat('markdown')
      toast('当前状态速查已生成并保存为新版本')
      const [fresh, sources] = await Promise.all([api<CurrentState>(base), api<SourceContext>(`${base}/context`)])
      if (alive.current) { if (fresh.version?.id === version.id) setCurrent(fresh); setContext(sources) }
    } catch (exc) { if (alive.current) setError(String(exc)) }
    finally { if (alive.current) setGenerating(false) }
  }
  const saveSettings = async () => {
    if (!settings || busy) return
    setBusy(true); setSettingsError('')
    try {
      const saved = await api<StateSettings>(`${base}/settings`, { method: 'PUT', body: JSON.stringify({ prompt, base_revision: settings.revision }) })
      if (!alive.current) return
      setSettings(saved); setPrompt(saved.prompt); toast('本书提示词已保存')
    } catch (exc) { if (alive.current) setSettingsError(String(exc)) }
    finally { if (alive.current) setBusy(false) }
  }
  const closeSettings = () => {
    if (busy || (promptDirty && !window.confirm('放弃未保存的提示词修改？'))) return
    setPrompt(settings?.prompt || ''); setSettingsOpen(false); setSettingsError('')
  }
  const inspect = async (version: StateVersion) => {
    try { const result = await api<StateVersion>(`${base}/versions/${version.id}`); if (alive.current) setPreview(result) }
    catch (exc) { if (alive.current) setError(String(exc)) }
  }
  const running = generating || !!current?.generating
  if (loading) return <Loading text="正在读取当前状态速查…" />
  return <div className="mx-auto max-w-7xl p-5 lg:p-7">
    <PageHeader title="当前状态速查" description="按本书最新已保存大纲全文、最后 5 章正文与全部已有章节梗概整理，生成后自动保存历史，可继续手动编辑。" actions={<>
      <Button variant="ghost" disabled={busy || running} onClick={() => setSettingsOpen(true)}><Settings2 size={14} />页面设置</Button>
      <Button variant="secondary" disabled={!current || busy || running || !dirty} onClick={() => void save()}><Save size={14} />保存修改</Button>
      <Button variant="primary" disabled={!current || busy || running || dirty || promptDirty || !context?.body_chapters.length} title={dirty ? '请先保存或放弃速查修改' : '使用已保存提示词与当前启用模型生成'} onClick={() => void generate()}><Sparkles size={14} />{running ? '正在生成…' : '生成当前状态速查'}</Button>
    </>} />
    {error && <div className="mt-4"><InlineError message={error} /></div>}
    <div className="my-4 rounded-md border border-line-soft bg-paper-raised px-4 py-3 text-meta leading-6 text-ink-soft">
      <div>正文范围：{context?.body_chapters.map(c => `第 ${c.chapter_number} 章`).join('、') || '暂无章节'} · 梗概 {context?.summary_count || 0} / {context?.chapter_count || 0} 章 · 输入约 {context?.input_characters.toLocaleString() || 0} 字符</div>
      <div>大纲：{context?.outline ? `已纳入 v${context.outline.version_number} 全文 · ${context.outline.characters.toLocaleString()} 字符${context.outline.is_empty ? "（内容为空）" : ""}` : "尚未保存，将按现有正文与梗概整理"}。大纲中的未来剧情仅作规划参考。</div>
      <div>模型：{context?.model || '尚未启用，请到 LLM 配置设置'}。按当前章节顺序选取；输入完整发送，不自动截断。</div>
      {!!context?.max_output_tokens && <div>最大输出：{context.max_output_tokens.toLocaleString()} Tokens（在 LLM 配置修改）。这是生成结果的上限，不是输入容量；上面的输入字符数也不等于 Token 数。</div>}
      {!!context?.timeout_seconds && <div>等待模型响应：{Number((context.timeout_seconds / 60).toFixed(1))} 分钟，可在 LLM 配置延长；修改后用于下一次请求。</div>}
      {!!context?.missing_summary_chapter_ids.length && <div>有 {context.missing_summary_chapter_ids.length} 章尚无梗概，将标明资料缺失。</div>}
      {!!context?.stale_summary_chapter_ids.length && <div className="text-warn-strong">有 {context.stale_summary_chapter_ids.length} 章梗概可能过时，建议先在正文页更新。</div>}
      {running && <div>等待模型返回。离开页面后可以返回查看结果；请勿重复生成。</div>}
    </div>
    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_260px]">
      <section className="min-w-0 rounded-lg border border-line bg-paper-raised">
        <div className="flex flex-wrap items-center gap-3 border-b border-line-soft px-5 py-3">
          <span className="text-body font-medium">状态速查全文</span>
          <MetaText>{current?.version ? `当前 v${current.version.version_number}` : '尚未保存'}</MetaText>
          {dirty && <Badge tone="warn">有未保存修改</Badge>}
          {current?.is_stale && <Badge tone="warn">来源已变化，建议重新生成</Badge>}
          <Button size="sm" variant="ghost" className="ml-auto" disabled={busy || running} onClick={() => { if ((!dirty && !promptDirty) || window.confirm('放弃未保存修改并重新读取？')) void load() }}><RefreshCw size={12} />重新读取</Button>
        </div>
        {!content && <MetaText className="px-5 pt-4">点击生成整理本书状态，也可以切换“原始”手动填写。未知信息请标为待补充。</MetaText>}
        <TextEditor aria-label="当前状态速查全文" value={content} disabled={!current || busy || running} onChange={event => setContent(event.target.value)} placeholder="## 当前进度\n\n## 最近五章\n\n## 主要人物当前状态\n\n## 待兑现伏笔\n\n## 下一章承接与风险\n\n## 写作约束与待核实项" className="paper-text min-h-[55vh] w-full border-0 bg-transparent px-5 py-5 shadow-none sm:px-7" />
        {current?.version && <div className="border-t border-line-soft px-5 py-3 text-meta text-ink-faint">{current.version.model || '手动整理'} · {new Date(current.version.created_at).toLocaleString()} · {current.version.source === 'llm' ? 'LLM 生成，请结合原文复核' : '保存的速查版本'}</div>}
      </section>
      <div className="min-w-0 space-y-4">
        <Panel title={<span className="inline-flex items-center gap-2"><History size={14} />速查历史 · {versions.length}</span>}>
          <div className="max-h-[55vh] space-y-2 overflow-y-auto">
            {!versions.length ? <Empty compact title="暂无历史" text="生成或保存后将在这里保留版本。" /> : versions.map(version => <div key={version.id} className="rounded border border-line-soft p-3">
              <div className="text-body">v{version.version_number} {version.id === current?.version?.id && <Badge tone="accent">当前</Badge>}</div>
              <MetaText>{new Date(version.created_at).toLocaleString()}</MetaText>
              <div className="mt-2 flex gap-1"><Button size="sm" variant="ghost" onClick={() => void inspect(version)}>查看 v{version.version_number}</Button>{version.id !== current?.version?.id && <Button size="sm" variant="ghost" disabled={dirty || busy || running} onClick={() => setRestoring(version)}>恢复</Button>}</div>
            </div>)}
          </div>
        </Panel>
        <Panel title="供其他项目读取">
          <MetaText>JSON 包含正文、版本、来源与过时标记；Markdown 接口返回已保存全文。</MetaText>
          <div className="mt-3 flex items-center justify-between text-meta"><a href={`${API_BASE}${base}`} target="_blank" rel="noreferrer" className="text-accent-700">读取 JSON</a><CopyButton text={`${API_BASE}${base}`} label="JSON 接口地址" /></div>
          <div className="mt-2 flex items-center justify-between text-meta"><a href={`${API_BASE}${base}/export.md`} download className="text-accent-700">下载 Markdown</a><CopyButton text={`${API_BASE}${base}/export.md`} label="Markdown 接口地址" /></div>
        </Panel>
      </div>
    </div>
    <Modal open={settingsOpen} onClose={closeSettings} title="当前状态速查 · 页面设置" description="提示词仅影响本书，保存后用于下一次生成。模型和超时使用全局 LLM 配置。" width="max-w-4xl" footer={<><Button variant="ghost" disabled={busy} onClick={() => { setPrompt(settings?.default_prompt || ''); setSettingsError('') }}>填入默认提示词</Button><Button variant="primary" disabled={busy || !prompt.trim() || !promptDirty} onClick={() => void saveSettings()}>保存提示词</Button></>}>
      {settingsError && <InlineError message={settingsError} />}
      <Textarea aria-label="当前状态速查提示词" rows={18} value={prompt} disabled={busy} onChange={event => setPrompt(event.target.value)} />
      <MetaText className="mt-3">最新已保存大纲全文、最后五章正文与全部已有梗概由系统另行发送，无需在提示词中粘贴。修改提示词不会改变固定的取材范围；系统会附加大纲规划与已发生事实的区分说明。</MetaText>
    </Modal>
    <Modal open={!!preview} onClose={() => setPreview(null)} title={`速查历史 · v${preview?.version_number}`} width="max-w-4xl" footer={<Button variant="ghost" onClick={() => setPreview(null)}>关闭</Button>}>
      <div className="mb-4"><FormatSwitch /></div>
      <TextContent label="历史状态速查全文" text={preview?.content || ''} className="paper-text" />
      {preview?.prompt && <details className="mt-6"><summary className="cursor-pointer text-meta">查看生成时使用的提示词</summary><TextContent label="历史生成提示词" text={preview.prompt} className="mt-3 text-meta" /></details>}
    </Modal>
    <ConfirmModal open={!!restoring} onClose={() => setRestoring(null)} title={`恢复速查 v${restoring?.version_number}？`} description="将创建新版本，保留所有历史。旧稿的来源信息也会保留，可能提示来源已变化。" confirmText="恢复为新版本" busy={busy} onConfirm={() => restoring && void save(restoring)} />
  </div>
}
