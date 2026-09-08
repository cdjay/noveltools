import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { FileText, History, Plus, RefreshCw, Save } from 'lucide-react'
import { api, API_BASE } from '../api/client'
import { useAppStore } from '../stores/useAppStore'
import { useUiPrefs } from '../stores/useUiPrefs'
import { useUnsavedChanges } from '../lib/useUnsavedChanges'
import { CopyButton } from '../lib/CopyButton'
import { FormatSwitch, TextContent, TextEditor } from '../lib/TextContent'
import { Badge, Button, ConfirmModal, Empty, Field, InlineError, Input, Loading, MetaText, Modal, PageHeader, Panel, toast } from '../lib/ui'

type MaterialVersion = { id: number; book_id: number; material_key: string; version_number: number; title: string; content: string; source: string; note: string; created_at: string }
type MaterialMeta = Omit<MaterialVersion, 'content'>
type Material = { material_key: string; title: string; version: MaterialVersion | null; template: string }
type MaterialItem = { material_key: string; title: string; version_id: number | null; version_number: number }
const dateText = (value: string) => new Date(/[Zz]|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z').toLocaleString()

export function MaterialsPage() {
  const { materialKey = 'writing-rules' } = useParams()
  const bookId = useAppStore(state => state.currentBook?.id)
  return bookId ? <MaterialEditor key={`${bookId}:${materialKey}`} bookId={bookId} materialKey={materialKey} /> : null
}

function MaterialEditor({ bookId, materialKey }: { bookId: number; materialKey: string }) {
  const navigate = useNavigate()
  const setTextFormat = useUiPrefs(state => state.setTextFormat)
  const base = `/books/${bookId}/materials`
  const url = `${base}/${materialKey}`
  const [items, setItems] = useState<MaterialItem[]>([])
  const [current, setCurrent] = useState<Material | null>(null)
  const [versions, setVersions] = useState<MaterialMeta[]>([])
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [createError, setCreateError] = useState('')
  const [preview, setPreview] = useState<MaterialVersion | null>(null)
  const [restoring, setRestoring] = useState<MaterialMeta | null>(null)
  const sequence = useRef(0)
  const alive = useRef(false)
  const dirty = !!current && (title !== current.title || content !== (current.version?.content || ''))
  useUnsavedChanges(dirty || (creating && !!newTitle.trim() && !busy))

  const load = async () => {
    const request = ++sequence.current
    setLoading(true); setError('')
    try {
      const [list, doc, history] = await Promise.all([api<MaterialItem[]>(base), api<Material>(url), api<MaterialMeta[]>(url + '/versions')])
      if (!alive.current || request !== sequence.current) return
      setItems(list); setCurrent(doc); setTitle(doc.title); setContent(doc.version?.content || ''); setVersions(history)
    } catch (exc) { if (alive.current && request === sequence.current) setError(String(exc)) }
    finally { if (alive.current && request === sequence.current) setLoading(false) }
  }
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; sequence.current++ } }, [url])

  const save = async (old?: MaterialMeta) => {
    if (!current || busy) return
    setBusy(true); setError('')
    try {
      const version = await api<MaterialVersion>(old ? `${url}/versions/${old.id}/restore` : url + '/versions', {
        method: 'POST', body: JSON.stringify({ title, content, base_version_id: current.version?.id ?? null }),
      })
      if (!alive.current) return
      setCurrent({ ...current, title: version.title, version }); setTitle(version.title); setContent(version.content)
      setVersions(previous => [version, ...previous]); setRestoring(null)
      setItems(previous => previous.map(item => item.material_key === materialKey ? { ...item, title: version.title, version_id: version.id, version_number: version.version_number } : item))
      toast(old ? '旧资料已恢复为新版本' : '资料已保存')
    } catch (exc) { if (alive.current) { setError(String(exc)); setRestoring(null) } }
    finally { if (alive.current) setBusy(false) }
  }
  const create = async () => {
    if (busy || dirty || !newTitle.trim()) return
    setBusy(true); setCreateError('')
    try {
      const doc = await api<MaterialVersion>(base, { method: 'POST', body: JSON.stringify({ title: newTitle, content: '' }) })
      if (!alive.current) return
      setCreating(false); setNewTitle(''); setTextFormat('raw')
      navigate(`/books/${bookId}/materials/${doc.material_key}`)
    } catch (exc) { if (alive.current) setCreateError(String(exc)) }
    finally { if (alive.current) setBusy(false) }
  }
  const inspect = async (version: MaterialMeta) => {
    try { const result = await api<MaterialVersion>(`${url}/versions/${version.id}`); if (alive.current) setPreview(result) }
    catch (exc) { if (alive.current) setError(String(exc)) }
  }
  if (loading) return <Loading text="正在读取书籍资料…" />
  return <div className="mx-auto max-w-7xl p-5 lg:p-7">
    <PageHeader title="书籍资料库" description="保存本书长期使用的规则和资料。资料由你维护，后续写前准备时再选择使用。" actions={<>
      <Button variant="secondary" disabled={busy || dirty} title={dirty ? '请先保存当前资料' : '创建一份可自由命名的资料'} onClick={() => { setCreating(true); setNewTitle(''); setCreateError('') }}><Plus size={14} />新建资料</Button>
      <Button variant="primary" disabled={!current || busy || !dirty || !title.trim()} onClick={() => void save()}><Save size={14} />{busy ? '保存中…' : '保存资料'}</Button>
    </>} />
    {error && <div className="my-4"><InlineError message={error} /></div>}
    {!current && <Button className="mt-4" onClick={() => void load()}>重新读取资料</Button>}
    <div className="mt-5 grid items-start gap-5 xl:grid-cols-[210px_minmax(0,1fr)_220px]">
      <Panel title="本书资料">
        <nav aria-label="本书资料列表" className="space-y-1">
          {items.map(item => <button key={item.material_key} disabled={busy} aria-pressed={item.material_key === materialKey} onClick={() => navigate(`/books/${bookId}/materials/${item.material_key}`)} className={`flex w-full items-start gap-2 rounded px-3 py-2 text-left text-body ${item.material_key === materialKey ? 'bg-accent-50 text-accent-700' : 'hover:bg-paper-sunken'}`}>
            <FileText size={14} className="mt-1 shrink-0" /><span className="min-w-0 break-words">{item.title}<span className="block text-meta text-ink-faint">{item.version_number ? `v${item.version_number}` : '尚未填写'}</span></span>
          </button>)}
        </nav>
      </Panel>
      <section className="min-w-0 rounded-lg border border-line bg-paper-raised">
        <div className="flex flex-wrap items-center gap-3 border-b border-line-soft px-5 py-3">
          <span className="font-medium">{current?.title || '资料全文'}</span><MetaText>{current?.version ? `当前 v${current.version.version_number}` : '尚未保存'}</MetaText>
          {dirty && <Badge tone="warn">有未保存修改</Badge>}
          <Button size="sm" variant="ghost" className="ml-auto" disabled={busy} onClick={() => { if (!dirty || window.confirm('放弃未保存修改并重新读取？')) void load() }}><RefreshCw size={12} />重新读取</Button>
        </div>
        {materialKey !== 'writing-rules' && <div className="px-5 pt-4"><Field label="资料标题"><Input value={title} maxLength={120} disabled={!current || busy} onChange={event => setTitle(event.target.value)} /></Field></div>}
        {current?.template && <div className="flex flex-wrap items-center gap-2 px-5 pt-4"><Button size="sm" variant="secondary" disabled={busy} onClick={() => { if (!content || window.confirm('用填写模板替换当前内容？保存后才会生成新版本。')) { setContent(current.template); setTextFormat('raw') } }}>填入写作规则模板</Button><MetaText>区分必须遵守的要求与建议；尚未确定的部分可留空。</MetaText></div>}
        <TextEditor aria-label="资料正文" value={content} disabled={!current || busy} onChange={event => setContent(event.target.value)} placeholder="在这里填写本书资料，支持 Markdown。" className="paper-text min-h-[55vh] w-full resize-y border-0 bg-transparent px-5 py-5 shadow-none sm:px-7" />
        <div className="border-t border-line-soft px-5 py-3"><MetaText>{content.replace(/\s/g, '').length.toLocaleString()} 字 · 原始 / Markdown 切换保留同一份内容</MetaText></div>
      </section>
      <div className="min-w-0 space-y-4">
        <Panel title={<span className="inline-flex items-center gap-2"><History size={14} />资料历史 · {versions.length}</span>}>
          <div className="max-h-[55vh] space-y-2 overflow-y-auto">
            {!versions.length ? <Empty compact title="暂无历史" text="保存后保留标题与全文版本。" /> : versions.map(version => <div key={version.id} className="rounded border border-line-soft p-3">
              <div>v{version.version_number} {version.id === current?.version?.id && <Badge tone="accent">当前</Badge>}</div>
              <MetaText>{dateText(version.created_at)}</MetaText>
              <div className="mt-2 flex flex-wrap gap-1"><Button size="sm" variant="ghost" onClick={() => void inspect(version)}>查看 v{version.version_number}</Button>{version.id !== current?.version?.id && <Button size="sm" variant="ghost" disabled={dirty || busy} onClick={() => setRestoring(version)}>恢复 v{version.version_number}</Button>}</div>
            </div>)}
          </div>
        </Panel>
        <Panel title="供其他项目读取">
          <MetaText>接口提供已保存资料，未保存草稿不会对外返回。</MetaText>
          <div className="mt-3 flex items-center justify-between text-meta"><a href={`${API_BASE}${url}`} target="_blank" rel="noreferrer" className="text-accent-700">读取资料 JSON</a><CopyButton text={`${API_BASE}${url}`} label="资料 JSON 接口地址" /></div>
          {current?.version && <div className="mt-2 flex items-center justify-between text-meta"><a href={`${API_BASE}${url}/export.md`} download className="text-accent-700">下载资料 Markdown</a><CopyButton text={`${API_BASE}${url}/export.md`} label="资料 Markdown 接口地址" /></div>}
        </Panel>
      </div>
    </div>
    <Modal open={creating} onClose={() => { if (!busy && (!newTitle || window.confirm('放弃新资料标题？'))) setCreating(false) }} title="新建资料" description="创建一份普通文本资料，标题与正文都可以继续修改并保留历史。" footer={<Button variant="primary" disabled={busy || !newTitle.trim()} onClick={() => void create()}>创建资料</Button>}>
      {createError && <InlineError message={createError} />}<Field label="新资料标题"><Input autoFocus maxLength={120} value={newTitle} disabled={busy} onChange={event => setNewTitle(event.target.value)} /></Field>
    </Modal>
    <Modal open={!!preview} onClose={() => setPreview(null)} title={`资料历史 · v${preview?.version_number}`} width="max-w-4xl" footer={<Button variant="ghost" onClick={() => setPreview(null)}>关闭</Button>}>
      <div className="mb-4 flex items-center justify-between gap-3"><strong>{preview?.title}</strong><FormatSwitch /></div>
      <TextContent text={preview?.content || ''} label="历史资料全文" className="paper-text" />
    </Modal>
    <ConfirmModal open={!!restoring} onClose={() => { if (!busy) setRestoring(null) }} busy={busy} title={`恢复资料 v${restoring?.version_number}？`} description="标题和正文将一起恢复为新版本，全部历史保留。" confirmText="恢复为新版本" onConfirm={() => restoring && void save(restoring)} />
  </div>
}
