// Цели Метрики (список — docs/landing-plan.md, раздел 8). Без счётчика — тихо ничего не делаем.
export function goal(name, params) {
  try {
    const id = window.__metrika
    if (id && typeof window.ym === 'function') window.ym(id, 'reachGoal', name, params)
  } catch {
    // Метрика заблокирована — не мешаем сайту
  }
}
