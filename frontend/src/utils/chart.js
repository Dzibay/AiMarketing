/** Общие расчёты для графиков: значения ряда, «круглая» шкала и плавные SVG-пути. */

/** Значения метрики по дням; пустое значение (CPA без конверсий) — null, на графике это разрыв линии. */
export function seriesValues(rows, key) {
  return (rows || []).map((row) => {
    const v = row?.[key]
    return v === null || v === undefined || v === '' ? null : Number(v)
  })
}

/** Деления оси Y от нуля: 1, 2, 2,5 или 5 × 10ⁿ, чтобы подписи были круглыми. */
export function niceTicks(max, count = 3) {
  if (!(max > 0)) return [0]
  const raw = max / count
  const exp = 10 ** Math.floor(Math.log10(raw))
  const f = raw / exp
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * exp
  const top = Math.ceil(max / step - 1e-9)
  return Array.from({ length: top + 1 }, (_, i) => +(i * step).toPrecision(12))
}

/** Режет точки на непрерывные куски: точка с y = null — разрыв. */
export function segments(points) {
  const out = []
  let cur = []
  for (const p of points) {
    if (p.y === null || p.y === undefined) {
      if (cur.length) out.push(cur)
      cur = []
    } else {
      cur.push(p)
    }
  }
  if (cur.length) out.push(cur)
  return out
}

const r1 = (v) => Math.round(v * 10) / 10

/** Монотонная кубическая кривая (как d3.curveMonotoneX): плавно, но без горбов выше и ниже данных. */
export function smoothPath(pts) {
  const n = pts.length
  if (!n) return ''
  if (n < 3) return pts.map((p, i) => `${i ? 'L' : 'M'}${r1(p.x)},${r1(p.y)}`).join('')
  const dx = []
  const m = []
  for (let i = 0; i < n - 1; i += 1) {
    dx[i] = pts[i + 1].x - pts[i].x
    m[i] = (pts[i + 1].y - pts[i].y) / dx[i]
  }
  const t = [m[0]]
  for (let i = 1; i < n - 1; i += 1) {
    t[i] = m[i - 1] * m[i] <= 0 ? 0 : (3 * (dx[i - 1] + dx[i])) / ((2 * dx[i] + dx[i - 1]) / m[i - 1] + (dx[i] + 2 * dx[i - 1]) / m[i])
  }
  t[n - 1] = m[n - 2]
  let d = `M${r1(pts[0].x)},${r1(pts[0].y)}`
  for (let i = 0; i < n - 1; i += 1) {
    const h = dx[i] / 3
    d += `C${r1(pts[i].x + h)},${r1(pts[i].y + h * t[i])} ${r1(pts[i + 1].x - h)},${r1(pts[i + 1].y - h * t[i + 1])} ${r1(pts[i + 1].x)},${r1(pts[i + 1].y)}`
  }
  return d
}

export function linePath(points) {
  return segments(points).map(smoothPath).join('')
}

/** Заливка под линией до baseY — по куску на каждый непрерывный участок. */
export function areaPath(points, baseY) {
  return segments(points)
    .filter((seg) => seg.length > 1)
    .map((seg) => `${smoothPath(seg)}L${r1(seg[seg.length - 1].x)},${baseY}L${r1(seg[0].x)},${baseY}Z`)
    .join('')
}

/** Одиночные точки между разрывами: линией их не нарисовать, показываем кружком. */
export function isolatedPoints(points) {
  return segments(points)
    .filter((seg) => seg.length === 1)
    .map((seg) => seg[0])
}
