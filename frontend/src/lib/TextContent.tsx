import { lazy, Suspense, type CSSProperties, type TextareaHTMLAttributes } from 'react'
import { useUiPrefs } from '../stores/useUiPrefs'
import { CopyButton } from './CopyButton'
import { Textarea } from './ui'

export function FormatSwitch() {
  const { textFormat, setTextFormat } = useUiPrefs()
  return <div className="inline-flex shrink-0 rounded-md border border-line bg-paper-raised p-0.5 text-meta" role="group" aria-label="文本显示格式">
    {(['raw', 'markdown'] as const).map(value => <button type="button" key={value} aria-pressed={textFormat === value} className={`rounded px-2 py-1 ${textFormat === value ? 'bg-accent-600 text-white' : 'text-ink-soft hover:bg-paper-sunken'}`} onClick={() => setTextFormat(value)}>{value === 'raw' ? '原始' : 'Markdown'}</button>)}
  </div>
}

const RenderMarkdown = lazy(() => import('./MarkdownRenderer'))

/** Text is never converted on save; this is a presentation-only preference. */
export function TextContent({ text, label, className = '', style, copy = true }: { text: string; label: string; className?: string; style?: CSSProperties; copy?: boolean }) {
  const format = useUiPrefs(state => state.textFormat)
  return <div className={`relative min-w-0 ${className}`} style={style}>
    {copy && <CopyButton text={text} label={label} className="absolute right-0 top-0 z-10 bg-paper-raised/90" />}
    <div aria-label={label} className={`${format === 'markdown' ? 'markdown-text' : 'whitespace-pre-wrap break-words'} ${copy ? 'pr-8' : ''}`}>
      {format === 'markdown' ? <Suspense fallback={<span className="whitespace-pre-wrap">{text}</span>}><RenderMarkdown text={text} /></Suspense> : text}
    </div>
  </div>
}

export function TextEditor(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const format = useUiPrefs(state => state.textFormat)
  if (format === 'raw') return <Textarea {...props} />
  return <div className="grid min-w-0 grid-cols-1 lg:grid-cols-2">
    <div className="min-w-0"><Textarea {...props} /></div>
    <section className="min-w-0 border-t border-line-soft p-5 lg:border-l lg:border-t-0" aria-label="Markdown 实时预览">
      <div className="mb-3 text-meta text-ink-faint">Markdown 实时预览</div>
      <TextContent text={String(props.value ?? '')} label={`${props['aria-label'] || '文本'}预览`} className="paper-text" style={props.style} />
    </section>
  </div>
}
