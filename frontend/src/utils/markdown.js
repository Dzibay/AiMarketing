/**
 * Небольшой безопасный Markdown для ответов ИИ: сначала экранируем весь текст,
 * затем вставляем только свои теги. Заголовки, списки, таблицы, **жирный**, *курсив*, `код`,
 * ссылки: http(s) — в новой вкладке, пути кабинета (`/cabinet/plan`) — внутренние, с data-route: страница,
 * которая рисует разметку, сама переводит их в переход роутера.
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
    .replace(/\[([^\]]+)\]\((\/(?!\/)[^\s)]*)\)/g, '<a href="$2" data-route>$1</a>')
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

/** Число из ячейки («12 400 ₽», «3,5 %», «−8 %»); null — в ячейке текст. */
function cellNumber(text) {
  const clean = text
    .replace(/[\s  ₽%×]/g, '')
    .replace(/[−–]/g, '-')
    .replace(',', '.')
  if (!/^[-+]?\d*\.?\d+$/.test(clean)) return null
  return Number(clean)
}

/**
 * Клик по заголовку таблицы из renderMarkdown(…, { sortable: true }): строки переставляются в DOM.
 * Числа — сначала большие, текст — по алфавиту; повторный клик — наоборот. true — клик был по сортировке.
 */
export function sortMdTable(event) {
  const button = event.target instanceof Element ? event.target.closest('button.md-sort') : null
  if (!button) return false
  const th = button.closest('th')
  const table = th.closest('table')
  const body = table.tBodies[0]
  const col = th.cellIndex
  const rows = [...body.rows]
  const cells = rows.map((r) => r.cells[col]?.textContent.trim() ?? '')
  const numeric = cells.filter(Boolean).every((c) => cellNumber(c) !== null)
  const was = th.getAttribute('aria-sort')
  const desc = was === 'none' || !was ? numeric : was === 'ascending'
  for (const other of th.parentElement.cells) other.setAttribute('aria-sort', 'none')
  th.setAttribute('aria-sort', desc ? 'descending' : 'ascending')
  const dir = desc ? -1 : 1
  const order = rows.map((row, i) => ({ row, text: cells[i], value: numeric ? cellNumber(cells[i]) : null }))
  order.sort((a, b) => {
    if (numeric) {
      if (a.value === null) return 1
      if (b.value === null) return -1
      return dir * (a.value - b.value)
    }
    return dir * a.text.localeCompare(b.text, 'ru', { numeric: true })
  })
  for (const { row } of order) body.appendChild(row)
  return true
}

export function renderMarkdown(source, { sortable = false } = {}) {
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
      // Сортировка — когда есть что переставлять; заголовок становится кнопкой.
      const sort = sortable && rows.length > 1
      const th = (c) =>
        sort
          ? `<th aria-sort="none"><button type="button" class="md-sort">${inline(c)}<span class="md-sort__arrow" aria-hidden="true"></span></button></th>`
          : `<th>${inline(c)}</th>`
      out.push(
        `<div class="md-table${sort ? ' md-table--sortable' : ''}"><table><thead><tr>${head.map(th).join('')}</tr></thead>` +
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
