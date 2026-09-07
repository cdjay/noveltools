import { useEffect, useRef, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, CheckCircle2, Info, Loader2, X, XCircle } from 'lucide-react'

/* ---------- 按钮 ---------- */

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'subtle'
type ButtonSize = 'sm' | 'md'

const buttonVariants: Record<ButtonVariant, string> = {
  primary: 'bg-accent-600 text-white hover:bg-accent-700 active:bg-accent-800',
  secondary: 'border border-line-strong bg-paper-raised text-ink hover:border-accent-400 hover:text-accent-700',
  ghost: 'text-ink-soft hover:bg-paper-sunken hover:text-ink',
  danger: 'border border-danger/30 bg-paper-raised text-danger hover:bg-danger-soft',
  subtle: 'bg-accent-50 text-accent-700 hover:bg-accent-100',
}

export function Button({ className = '', variant = 'secondary', size = 'md', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: ButtonSize }) {
  const sizing = size === 'sm' ? 'px-2.5 py-1.5 text-meta gap-1' : 'px-3.5 py-2 text-body gap-1.5'
  return <button className={`inline-flex items-center justify-center rounded-md font-medium transition-colors duration-fast disabled:opacity-45 ${sizing} ${buttonVariants[variant]} ${className}`} {...props} />
}

/* ---------- 表单 ---------- */

const fieldControl = 'w-full rounded-md border border-line bg-paper-raised px-3 py-2 text-body text-ink outline-none transition-colors duration-fast placeholder:text-ink-faint hover:border-line-strong focus:border-accent-500'

export function Input({ className = '', ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`${fieldControl} ${className}`} {...props} />
}

export function Textarea({ className = '', ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`${fieldControl} leading-7 ${className}`} {...props} />
}

export function Select({ className = '', ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={`${fieldControl} cursor-pointer appearance-auto ${className}`} {...props} />
}

export function Field({ label, hint, children, className = '' }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return <label className={`block text-body text-ink-soft ${className}`}>
    <span className="mb-1.5 block text-[13px] font-medium">{label}</span>
    {children}
    {hint && <span className="mt-1 block text-meta text-ink-faint">{hint}</span>}
  </label>
}

/* ---------- 面板与版面 ---------- */

export function Panel({ title, description, action, children, className = '', bodyClassName = '', flush = false }: {
  title?: ReactNode; description?: string; action?: ReactNode; children: ReactNode; className?: string; bodyClassName?: string; flush?: boolean
}) {
  return <section className={`rounded-lg border border-line bg-paper-raised shadow-card ${className}`}>
    {(title || action) && <header className="flex items-center justify-between gap-3 border-b border-line-soft px-4 py-3">
      <div className="min-w-0">
        {title && <h2 className="truncate text-[15px] font-semibold text-ink">{title}</h2>}
        {description && <p className="mt-0.5 text-meta text-ink-faint">{description}</p>}
      </div>
      {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
    </header>}
    <div className={flush ? '' : `p-4 ${bodyClassName}`}>{children}</div>
  </section>
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return <div className="flex flex-wrap items-end justify-between gap-3">
    <div className="min-w-0">
      <h1 className="text-xl font-bold tracking-tight text-ink">{title}</h1>
      {description && <p className="mt-1 max-w-2xl text-body text-ink-faint">{description}</p>}
    </div>
    {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
  </div>
}

export function Stat({ label, value, tone = 'default' }: { label: string; value: ReactNode; tone?: 'default' | 'ok' | 'warn' | 'danger' }) {
  const tones = { default: 'text-ink', ok: 'text-ok-strong', warn: 'text-warn-strong', danger: 'text-danger-strong' }
  return <div className="rounded-md bg-paper-sunken px-3.5 py-3">
    <div className="text-meta text-ink-faint">{label}</div>
    <div className={`mt-1 text-xl font-bold tabular-nums ${tones[tone]}`}>{value}</div>
  </div>
}

/* ---------- 徽章与状态 ---------- */

export type BadgeTone = 'neutral' | 'ok' | 'warn' | 'danger' | 'accent'

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: BadgeTone }) {
  const tones: Record<BadgeTone, string> = {
    neutral: 'bg-paper-sunken text-ink-soft',
    ok: 'bg-ok-soft text-ok-strong',
    warn: 'bg-warn-soft text-warn-strong',
    danger: 'bg-danger-soft text-danger-strong',
    accent: 'bg-accent-50 text-accent-700',
  }
  return <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-meta font-medium ${tones[tone]}`}>{children}</span>
}

/** 统一把英文状态码转成作者能读懂的中文与色调 */
export const statusText: Record<string, string> = {
  draft: '草稿', ongoing: '连载', completed: '已完成', active: '在案', dead: '已死亡', archived: '归档',
  pending: '待确认', accepted: '已写入', auto_applied: '已自动入库', auto_skipped: '已跳过', rejected: '已拒绝',
  planned: '计划中', resolved: '已解决', open: '开放', failed: '失败', queued: '排队中', analyzing: '分析中',
  manual: '手动', import: '导入', revised: '恢复', auto: '自动', approved: '已审定',
}

export function statusTone(status: string): BadgeTone {
  if (['completed', 'accepted', 'auto_applied', 'active', 'resolved', 'approved'].includes(status)) return 'ok'
  if (['pending', 'analyzing', 'queued', 'ongoing', 'open', 'planned'].includes(status)) return 'warn'
  if (['failed', 'dead', 'rejected', 'auto_skipped'].includes(status)) return status === 'rejected' || status === 'auto_skipped' ? 'neutral' : 'danger'
  return 'neutral'
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return <Badge tone={statusTone(status)}>{label || statusText[status] || status}</Badge>
}

/* ---------- 空状态 / 加载 / 错误 ---------- */

export function Empty({ icon, title, text, action, compact = false }: { icon?: ReactNode; title: string; text?: string; action?: ReactNode; compact?: boolean }) {
  return <div className={`flex flex-col items-center justify-center rounded-lg border border-dashed border-line-strong bg-paper text-center ${compact ? 'px-4 py-6' : 'px-6 py-12'}`}>
    {icon && <div className="mb-3 text-ink-faint">{icon}</div>}
    <div className="text-body font-medium text-ink-soft">{title}</div>
    {text && <div className="mt-1.5 max-w-sm text-meta leading-6 text-ink-faint">{text}</div>}
    {action && <div className="mt-4">{action}</div>}
  </div>
}

export function Loading({ text = '正在加载…' }: { text?: string }) {
  return <div className="flex items-center justify-center gap-2 py-14 text-body text-ink-faint"><Loader2 size={16} className="animate-spin" />{text}</div>
}

export function InlineError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return <div className="flex items-start justify-between gap-3 rounded-md border border-danger/25 bg-danger-soft px-3.5 py-2.5 text-body text-danger-strong anim-fade" role="alert">
    <span className="min-w-0 break-words">{message}</span>
    {onRetry && <button className="shrink-0 text-meta underline underline-offset-2" onClick={onRetry}>重试</button>}
  </div>
}

export function InlineNotice({ message, tone = 'ok' }: { message: string; tone?: 'ok' | 'accent' | 'warn' }) {
  const tones = { ok: 'border-ok/25 bg-ok-soft text-ok-strong', accent: 'border-accent-200 bg-accent-50 text-accent-700', warn: 'border-warn/30 bg-warn-soft text-warn-strong' }
  return <div className={`rounded-md border px-3.5 py-2.5 text-body anim-fade ${tones[tone]}`} role="status">{message}</div>
}

/* ---------- 弹窗（含焦点管理与 Esc 关闭） ---------- */

export function Modal({ open, onClose, title, description, children, footer, width = 'max-w-2xl' }: {
  open: boolean; onClose: () => void; title: ReactNode; description?: string; children: ReactNode; footer?: ReactNode; width?: string
}) {
  const boxRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const box = boxRef.current
    const focusable = box?.querySelector<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
    focusable?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.stopPropagation(); closeRef.current() }
      if (event.key === 'Tab' && box) {
        const items = Array.from(box.querySelectorAll<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')).filter(el => !el.hasAttribute('disabled'))
        if (!items.length) return
        const first = items[0]; const last = items[items.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => { document.removeEventListener('keydown', onKey, true); previous?.focus() }
  }, [open])
  if (!open) return null
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/35 p-4 anim-fade" onMouseDown={event => event.target === event.currentTarget && onClose()}>
      <div ref={boxRef} role="dialog" aria-modal="true" className={`flex max-h-[88vh] w-full ${width} flex-col rounded-lg bg-paper-raised shadow-pop anim-rise`}>
        <header className="flex items-start justify-between gap-4 border-b border-line-soft px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
            {description && <p className="mt-1 text-meta leading-5 text-ink-faint">{description}</p>}
          </div>
          <button aria-label="关闭" className="rounded-md p-1.5 text-ink-faint transition-colors hover:bg-paper-sunken hover:text-ink" onClick={onClose}><X size={17} /></button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 scrollbar-thin">{children}</div>
        {footer && <footer className="flex items-center justify-end gap-2 border-t border-line-soft px-5 py-3.5">{footer}</footer>}
      </div>
    </div>,
    document.body,
  )
}

export function ConfirmModal({ open, onClose, onConfirm, title, description, confirmText = '确认', danger = false, busy = false }: {
  open: boolean; onClose: () => void; onConfirm: () => void; title: string; description?: ReactNode; confirmText?: string; danger?: boolean; busy?: boolean
}) {
  return <Modal open={open} onClose={onClose} title={title} width="max-w-md"
    footer={<>
      <Button variant="ghost" onClick={onClose}>取消</Button>
      <Button variant={danger ? 'danger' : 'primary'} className={danger ? '!bg-danger !text-white hover:!bg-danger-strong border-transparent' : ''} disabled={busy} onClick={onConfirm}>{busy ? '处理中…' : confirmText}</Button>
    </>}>
    <div className="text-body leading-7 text-ink-soft">{description}</div>
  </Modal>
}

/* ---------- 标签页（分段下划线样式） ---------- */

export function Tabs<T extends string>({ value, onChange, items, className = '' }: { value: T; onChange: (value: T) => void; items: [T, ReactNode][]; className?: string }) {
  return <div className={`flex flex-wrap gap-1 border-b border-line ${className}`} role="tablist">
    {items.map(([key, label]) => {
      const active = value === key
      return <button key={key} role="tab" aria-selected={active} onClick={() => onChange(key)}
        className={`-mb-px border-b-2 px-3 py-2 text-body font-medium transition-colors duration-fast ${active ? 'border-accent-600 text-accent-700' : 'border-transparent text-ink-faint hover:text-ink'}`}>
        {label}
      </button>
    })}
  </div>
}

/* ---------- 全局操作反馈 Toast ---------- */

type ToastItem = { id: number; message: string; tone: 'ok' | 'danger' | 'accent' }
let toastSeq = 0
type ToastListener = (items: ToastItem[]) => void
let toastItems: ToastItem[] = []
const toastListeners = new Set<ToastListener>()

export function toast(message: string, tone: ToastItem['tone'] = 'ok') {
  const item = { id: ++toastSeq, message, tone }
  toastItems = [...toastItems, item]
  toastListeners.forEach(listener => listener(toastItems))
  window.setTimeout(() => { toastItems = toastItems.filter(x => x.id !== item.id); toastListeners.forEach(listener => listener(toastItems)) }, 3200)
}

export function ToastHost() {
  const [items, setItems] = useState<ToastItem[]>(toastItems)
  useEffect(() => { const listener: ToastListener = setItems; toastListeners.add(listener); return () => { toastListeners.delete(listener) } }, [])
  if (!items.length) return null
  const icons = { ok: <CheckCircle2 size={15} className="text-ok" />, danger: <XCircle size={15} className="text-danger" />, accent: <Info size={15} className="text-accent-600" /> }
  return createPortal(
    <div className="fixed bottom-5 right-5 z-[60] flex w-80 flex-col gap-2" aria-live="polite">
      {items.map(item => <div key={item.id} className="flex items-start gap-2 rounded-md border border-line bg-paper-raised px-3.5 py-2.5 text-body text-ink shadow-pop anim-slide-right">{icons[item.tone]}<span className="min-w-0 break-words">{item.message}</span></div>)}
    </div>,
    document.body,
  )
}

/* ---------- 杂项 ---------- */

export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return <div className="flex items-center justify-between gap-2">
    <h3 className="text-[13px] font-semibold uppercase tracking-wide text-ink-faint">{children}</h3>
    {action}
  </div>
}

export function MetaText({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`text-meta text-ink-faint ${className}`}>{children}</div>
}

export function DangerZoneNote({ children }: { children: ReactNode }) {
  return <div className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn-soft px-3.5 py-3 text-body leading-6 text-warn-strong"><AlertTriangle size={15} className="mt-1 shrink-0" /><div>{children}</div></div>
}
