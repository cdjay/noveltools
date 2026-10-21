export type Opinion = { key: string; kind: 'M' | 'Q' | 'K' | 'intro'; text: string }

/** Display-only repair for model-written field labels; never rewrite stored opinions. */
export function opinionMarkdown(text: string): string {
  let fence: string | null = null
  return text.split(/(?<=\n)/).map(line => {
    const marker = line.match(/^\s{0,3}(`{3,}|~{3,})/)
    if (marker) {
      if (!fence) fence = marker[1]
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = null
      return line
    }
    if (fence) return line
    return line.replace(/^(\s*(?:[-+*]\s+)?)\*\*[ \t]*([^*\n`]{1,60}[：:])[ \t]*\*\*[ \t]*/,
      '$1**$2** ')
  }).join('')
}

/** Keep every character; unnumbered prose and section headings remain editable too. */
export function splitOpinions(text: string): Opinion[] {
  const lines = text.split(/(?<=\n)/)
  const result: Opinion[] = []
  let current: Opinion = {key: '说明', kind: 'intro', text: ''}
  for (const line of lines) {
    const plain = line.replace(/\\([*#])/g, '$1').replace(/^[\s#]+/, '').replace(/\*\*/g, '').trim()
    const match = plain.match(/^([MQK]\d+)(?=$|[\s｜|：:、.])/)
    const section = !match && /^(?:【)?(?:主编意见|建议修改|待作者决定|重要保护项)(?:】)?$/.test(plain)
    if (match || section) {
      if (current.text) result.push(current)
      current = {key: match?.[1] || plain, kind: match ? match[1][0] as Opinion['kind'] : 'intro', text: ''}
    }
    current.text += line
  }
  if (current.text) result.push(current)
  return result.length ? result : [{key: '主编意见', kind: 'intro', text}]
}

export function opinionTone(item: Opinion): string {
  if (item.kind === 'K') return 'border-accent-200 bg-accent-50/50'
  if (item.kind === 'Q') return 'border-warn/40 bg-warn-soft/40'
  if (item.kind === 'M') return /确定问题/.test(item.text) ? 'border-danger/30 bg-danger-soft/40' : 'border-warn/30 bg-warn-soft/30'
  return 'border-line bg-paper'
}
