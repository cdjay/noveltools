import { memo, useRef, type ComponentProps } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CopyButton } from './CopyButton'

function CodeBlock(props: ComponentProps<'pre'>) {
  const ref = useRef<HTMLPreElement>(null)
  return <div className="relative"><CopyButton label="代码块" getText={() => ref.current?.textContent || ''} className="absolute right-1 top-1 bg-paper-raised/90" /><pre {...props} ref={ref} className="!pr-10" /></div>
}

export default memo(function MarkdownRenderer({ text }: { text: string }) {
  return <Markdown remarkPlugins={[remarkGfm]} components={{
    a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
    img: ({ node: _node, ...props }) => <img {...props} loading="lazy" referrerPolicy="no-referrer" />,
    table: ({ node: _node, ...props }) => <div className="md-table-scroll"><table {...props} /></div>,
    pre: ({ node: _node, ...props }) => <CodeBlock {...props} />,
  }}>{text}</Markdown>
})
