import { useRef, useState } from 'react'
import { Upload } from 'lucide-react'
import { api, upload } from '../api/client'
import type { Chapter } from '../types'
import { Button, InlineError, Input, MetaText, Modal, toast } from './ui'

type Preview = { encoding: string; chapters: { title: string; content: string }[] }

export function ImportChapters({ bookId, disabled, onImported }: { bookId: number; disabled: boolean; onImported: (chapters: Chapter[]) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [split, setSplit] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState(0)
  const inspect = async (nextFile: File, nextSplit: boolean) => {
    setBusy(true); setError(''); setFile(nextFile); setSplit(nextSplit)
    try {
      setPreview(await upload<Preview>(`/books/${bookId}/chapters/import/preview?split=${nextSplit}`, nextFile))
      setSelected(0)
    } catch (exc) { setError(String(exc)); setPreview(null) }
    finally { setBusy(false) }
  }
  const confirm = async () => {
    if (!preview) return
    setBusy(true); setError('')
    try {
      const chapters = await api<Chapter[]>(`/books/${bookId}/chapters/import/confirm`, { method: 'POST', body: JSON.stringify({ chapters: preview.chapters }) })
      onImported(chapters); setFile(null); setPreview(null); toast(`已导入 ${chapters.length} 个章节`)
    } catch (exc) { setError(String(exc)) }
    finally { setBusy(false) }
  }
  return <>
    <input ref={input} type="file" accept=".txt,.md,.markdown" className="hidden" aria-label="导入正文文件" onChange={event => { const chosen = event.target.files?.[0]; event.target.value = ''; if (chosen) void inspect(chosen, true) }} />
    <Button variant="ghost" size="sm" disabled={disabled} onClick={() => input.current?.click()}><Upload size={13} />导入 TXT / Markdown</Button>
    <Modal open={!!file} onClose={() => { if (!busy) setFile(null) }} title="导入正文预览" description="确认章节边界后再导入，也可以保留为单章。" width="max-w-4xl"
      footer={<><Button variant="ghost" disabled={busy} onClick={() => setFile(null)}>取消</Button><Button variant="primary" disabled={busy || !preview} onClick={() => void confirm()}>{busy ? '处理中…' : `确认导入 ${preview?.chapters.length || 0} 章`}</Button></>}>
      {error && <InlineError message={error} />}
      <label className="flex gap-2 text-body"><input type="checkbox" checked={split} disabled={busy} onChange={event => file && void inspect(file, event.target.checked)} />按章节标题自动拆分</label>
      {preview && <>
        <MetaText className="my-3">{file?.name} · 编码 {preview.encoding} · {preview.chapters.length} 章</MetaText>
        <div className="grid grid-cols-[200px_minmax(0,1fr)] gap-4">
          <div className="max-h-80 overflow-y-auto">{preview.chapters.map((chapter, index) => <button key={index} className={`block w-full rounded p-2 text-left text-meta ${selected === index ? 'bg-accent-50' : ''}`} onClick={() => setSelected(index)}>{index + 1}. {chapter.title}</button>)}</div>
          <div><Input aria-label="导入章节标题" value={preview.chapters[selected]?.title || ''} onChange={event => setPreview({ ...preview, chapters: preview.chapters.map((chapter, index) => index === selected ? { ...chapter, title: event.target.value } : chapter) })} /><div className="paper-text mt-3 max-h-80 overflow-y-auto whitespace-pre-wrap" aria-label="导入章节预览">{preview.chapters[selected]?.content}</div></div>
        </div>
      </>}
    </Modal>
  </>
}
