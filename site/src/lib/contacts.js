// Контакты, реквизиты, ключ капчи — из админки (вкладка «Сайт»): GET /api/site/public.
// При сборке — через SITE_API_URL (нет API — пусто, строки не выводятся); в браузере Base.astro дочитывает
// свежие значения и заполняет элементы с data-contact, так что пересобирать сайт после правки не нужно.
export const EMPTY_PUBLIC = { email: '', legal: '', telegram: '', captcha_key: '', ai_tools: false }

let cached = null

export async function loadPublic() {
  if (cached) return cached
  const base = import.meta.env.SITE_API_URL || process.env.SITE_API_URL
  if (!base) return (cached = EMPTY_PUBLIC)
  try {
    const r = await fetch(`${base.replace(/\/$/, '')}/api/site/public`, { signal: AbortSignal.timeout(5000) })
    cached = r.ok ? { ...EMPTY_PUBLIC, ...(await r.json()) } : EMPTY_PUBLIC
  } catch {
    cached = EMPTY_PUBLIC
  }
  return cached
}

// Ссылка mailto с темой письма (data-subject у элемента — та же тема в браузере).
export const mailto = (email, subject = '') =>
  email ? `mailto:${email}${subject ? `?subject=${encodeURIComponent(subject)}` : ''}` : ''
