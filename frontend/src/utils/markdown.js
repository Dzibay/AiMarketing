/**
 * Небольшой безопасный Markdown для ответов ИИ: сначала экранируем весь текст,
 * затем вставляем только свои теги. Заголовки, списки, таблицы, **жирный**, *курсив*, `код`,
 * ссылки только http(s).
 */

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function inline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
}

function tableCells(line) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim())
}

const isTableSep = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line)

export function renderMarkdown(source) {
  const lines = String(source || '').replace(/\r\n/g, '\n').split('\n')
  const out = []
  let para = []
  let list = null

  const flushPara = () => {
    if (para.length) out.push(`<p>${para.map(inline).join('<br>')}</p>`)
    para = []
  }
  const flushList = () => {
    if (list) out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join('')}</${list.tag}>`)
    list = null
  }
  const flush = () => {
    flushPara()
    flushList()
  }

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]

    if (/^\s*```/.test(line)) {
      flush()
      const code = []
      i += 1
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        code.push(lines[i])
        i += 1
      }
      out.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`)
      continue
    }

    if (line.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      flush()
      const head = tableCells(line)
      const rows = []
      i += 2
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(tableCells(lines[i]))
        i += 1
      }
      i -= 1
      out.push(
        `<div class="md-table"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead>` +
          `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`,
      )
      continue
    }

    const heading = /^\s*(#{1,4})\s+(.*)$/.exec(line)
    if (heading) {
      flush()
      const level = Math.min(heading[1].length + 2, 6)
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`)
      continue
    }

    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line)
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line)
    if (bullet || numbered) {
      flushPara()
      const tag = bullet ? 'ul' : 'ol'
      if (list && list.tag !== tag) flushList()
      if (!list) list = { tag, items: [] }
      list.items.push((bullet || numbered)[1])
      continue
    }

    if (!line.trim()) {
      flush()
      continue
    }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
      flush()
      out.push('<hr>')
      continue
    }
    flushList()
    para.push(line)
  }
  flush()
  return out.join('')
}
