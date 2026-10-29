// Одинаковые названия кампаний (например, копия) в выпадающих списках различаем номером из Директа.

export function repeatedNames(names) {
  const seen = new Set()
  const twice = new Set()
  for (const n of names) (seen.has(n) ? twice : seen).add(n)
  return twice
}

export function campaignLabel(c, repeated = new Set()) {
  const name = c.name || 'Без названия'
  return repeated.has(c.name) && c.campaign_id ? `${name} · №${c.campaign_id}` : name
}
