import { useEffect, useState } from 'react'
import { History, RotateCcw, Save } from 'lucide-react'
import { api } from '../api/client'
import { useAppStore } from '../stores/useAppStore'
import { useUnsavedChanges } from '../lib/useUnsavedChanges'
import type { OutlineVersion } from '../types'
import { Badge, Button, ConfirmModal, Empty, InlineError, Loading, MetaText, Modal, PageHeader, Textarea, toast } from '../lib/ui'

export function OutlinePage() {
  const { currentBook } = useAppStore()
  const [versions, setVersions] = useState<OutlineVersion[]>([])
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<OutlineVersion | null>(null)
  const [restoring, setRestoring] = useState<OutlineVersion | null>(null)
  const latest = versions[0]
  const dirty = content !== (latest?.content || '')
  useUnsavedChanges(dirty)

  const load = async () => {
    if (!currentBook) return
    setLoading(true); setError('')
    try {
      const data = await api<OutlineVersion[]>(`/books/${currentBook.id}/outline/versions`)
      setVersions(data); setContent(data[0]?.content || ''); setLoaded(true)
    } catch (exc) { setError(String(exc)) } finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [currentBook?.id])

  const save = async (restore?: OutlineVersion) => {
    if (!currentBook || busy) return
    setBusy(true); setError('')
    try {
      const base = `/books/${currentBook.id}/outline/versions`
      const saved = await api<OutlineVersion>(restore ? `${base}/${restore.id}/restore` : base, {
        method: 'POST', body: JSON.stringify({ content, base_version_id: latest?.id ?? null }),
      })
      setVersions(previous => [saved, ...previous]); setContent(saved.content)
      setPreview(null); setRestoring(null)
      toast(`大纲已保存为 v${saved.version_number}`)
    } catch (exc) { setError(String(exc)); setRestoring(null) } finally { setBusy(false) }
  }

  if (loading) return <Loading text="正在读取大纲…" />
  return <div className="mx-auto max-w-7xl p-5 lg:p-7">
    <PageHeader title="大纲" description="自由编写整篇大纲。每次保存保留一份完整历史，恢复旧稿也会生成新版本。" actions={
      <Button variant="primary" disabled={busy || !loaded || (!dirty && !!latest)} onClick={() => void save()}><Save size={14} />{busy ? '保存中…' : '保存大纲'}</Button>
    } />
    {error && <div className="mt-4"><InlineError message={error} onRetry={!dirty ? () => void load() : undefined} /></div>}
    <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_260px]">
      <section className="min-w-0 rounded-lg border border-line bg-paper-raised">
        <div className="flex flex-wrap items-center gap-3 border-b border-line-soft px-5 py-3">
          <span className="text-body font-medium">大纲全文</span>
          <MetaText>{latest ? `当前 v${latest.version_number}` : '尚未保存'}</MetaText>
          {dirty && <Badge tone="warn">有未保存修改</Badge>}
          <MetaText className="ml-auto tabular-nums">{content.replace(/\s/g, '').length.toLocaleString()} 字</MetaText>
        </div>
        <Textarea aria-label="大纲全文" disabled={busy || !loaded} value={content} onChange={e => setContent(e.target.value)} placeholder="在这里自由编写故事大纲，无需划分大纲块…"
          className="paper-text min-h-[65vh] w-full resize-y rounded-none border-0 bg-transparent px-5 py-5 text-[16px] shadow-none focus:border-transparent sm:px-7" />
      </section>
      <aside aria-label="大纲历史" className="self-start rounded-lg border border-line bg-paper-raised">
        <div className="flex items-center gap-2 border-b border-line-soft px-4 py-3 text-body font-medium"><History size={15} />历史版本 <MetaText>· {versions.length}</MetaText></div>
        <div className="max-h-[65vh] space-y-2 overflow-y-auto p-3 scrollbar-thin">
          {!versions.length ? <Empty compact title="暂无历史版本" text="第一次保存后，完整大纲会保留在这里。" /> : versions.map(version => <div key={version.id} className="rounded-md border border-line-soft p-3">
            <div className="flex items-center justify-between text-body"><strong>v{version.version_number}</strong>{version.id === latest?.id && <Badge tone="accent">当前</Badge>}</div>
            <MetaText className="mt-1">{new Date(version.created_at).toLocaleString()}</MetaText>
            {version.note && <MetaText className="mt-1 break-words">{version.note}</MetaText>}
            <div className="mt-2 flex flex-wrap gap-1">
              <Button size="sm" variant="ghost" onClick={() => setPreview(version)}>查看 v{version.version_number}</Button>
              {version.id !== latest?.id && <Button size="sm" variant="ghost" disabled={busy} onClick={() => setRestoring(version)}><RotateCcw size={12} />恢复 v{version.version_number}</Button>}
            </div>
          </div>)}
        </div>
      </aside>
    </div>
    <Modal open={!!preview} onClose={() => setPreview(null)} title={`大纲历史 · v${preview?.version_number}`} width="max-w-4xl" footer={<Button variant="ghost" onClick={() => setPreview(null)}>关闭</Button>}>
      <div className="paper-text min-h-40 whitespace-pre-wrap break-words" aria-label="历史大纲全文">{preview?.content || '此版本为空白大纲。'}</div>
    </Modal>
    <ConfirmModal open={!!restoring} onClose={() => { if (!busy) setRestoring(null) }} busy={busy} title={`恢复大纲 v${restoring?.version_number}？`} confirmText="恢复为新版本"
      description={`将以此版本全文创建新的历史版本，已有历史仍然保留。${dirty ? '当前未保存的编辑将被替换。' : ''}`} onConfirm={() => restoring && void save(restoring)} />
  </div>
}
