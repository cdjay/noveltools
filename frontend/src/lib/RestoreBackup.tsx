import { useRef, useState } from 'react'
import { upload } from '../api/client'
import type { Book } from '../types'
import { Button, InlineError, Modal, toast } from './ui'

export function RestoreBackup({ onRestored }: { onRestored: (book: Book) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<{ title: string; chapters: number; versions: number; outlines: number; current_states?: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const inspect = async (chosen: File) => {
    setFile(chosen); setPreview(null); setBusy(true); setError('')
    try { setPreview(await upload('/backups/preview', chosen)) }
    catch (exc) { setError(String(exc)) }
    finally { setBusy(false) }
  }
  const restore = async () => {
    if (!file) return
    setBusy(true); setError('')
    try {
      onRestored(await upload<Book>('/backups/restore', file)); setFile(null); toast('备份已恢复为新书籍')
    } catch (exc) { setError(String(exc)) }
    finally { setBusy(false) }
  }
  return <>
    <input ref={input} type="file" accept=".json" aria-label="书籍备份文件" className="hidden" onChange={event => { const chosen = event.target.files?.[0]; event.target.value = ''; if (chosen) void inspect(chosen) }} />
    <Button variant="secondary" onClick={() => input.current?.click()}>恢复书籍备份</Button>
    <Modal open={!!file} onClose={() => { if (!busy) setFile(null) }} title="恢复书籍备份" description="将创建独立的恢复副本，保留原书。备份包含已保存内容，不包含浏览器草稿与 API Key。"
      footer={<><Button variant="ghost" disabled={busy} onClick={() => setFile(null)}>取消</Button><Button variant="primary" disabled={busy || !preview} onClick={() => void restore()}>{busy ? '处理中…' : '恢复为新书籍'}</Button></>}>
      {error && <InlineError message={error} />}
      {preview && <p className="text-body leading-7">《{preview.title}》<br />{preview.chapters} 章 · {preview.versions} 份正文版本 · {preview.outlines} 份大纲版本 · {preview.current_states || 0} 份状态速查版本</p>}
    </Modal>
  </>
}
