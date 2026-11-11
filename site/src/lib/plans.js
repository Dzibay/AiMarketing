// Тарифы при сборке: GET /api/site/plans (SITE_API_URL). Нет API — пустой каталог, остров дочитает в браузере.
export const EMPTY_PLANS = { items: [], features: {}, trial: null }

let cached = null

export async function loadPlans() {
  if (cached) return cached
  const base = import.meta.env.SITE_API_URL || process.env.SITE_API_URL
  if (!base) return (cached = EMPTY_PLANS)
  try {
    const r = await fetch(`${base.replace(/\/$/, '')}/api/site/plans`, { signal: AbortSignal.timeout(5000) })
    cached = r.ok ? await r.json() : EMPTY_PLANS
  } catch {
    cached = EMPTY_PLANS
  }
  return cached
}

export function daysWord(n) {
  return n % 10 === 1 && n % 100 !== 11 ? 'день' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'дня' : 'дней'
}
