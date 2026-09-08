import { useEffect, useRef, useState } from 'react'
import { Check, Copy, X } from 'lucide-react'

export function CopyButton({ text, getText, label = '文本', className = '' }: { text?: string; getText?: () => string; label?: string; className?: string }) {
  const [status, setStatus] = useState<'idle' | 'done' | 'failed'>('idle')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(getText ? getText() : text || '')
      setStatus('done')
    } catch { setStatus('failed') }
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setStatus('idle'), 2200)
  }
  const message = status === 'done' ? '已复制' : status === 'failed' ? '复制失败，请手动选择文本复制' : `复制${label}`
  return <button type="button" aria-label={`复制${label}`} title={message} className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded text-ink-faint hover:bg-paper-sunken hover:text-accent-700 ${className}`}
    onClick={event => { event.preventDefault(); event.stopPropagation(); void copy() }}>
    {status === 'done' ? <Check size={14} /> : status === 'failed' ? <X size={14} /> : <Copy size={14} />}
    <span className="sr-only" role="status">{status === 'idle' ? '' : message}</span>
  </button>
}
