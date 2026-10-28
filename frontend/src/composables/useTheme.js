import { computed, ref } from 'vue'

const THEME_KEY = 'aimarketing-theme'
const COLORS = { light: '#eef3ef', dark: '#141a17' }

const media = window.matchMedia?.('(prefers-color-scheme: dark)')
const theme = ref(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light')

function stored() {
  try {
    const value = localStorage.getItem(THEME_KEY)
    return value === 'light' || value === 'dark' ? value : ''
  } catch {
    return ''
  }
}

function apply(value) {
  theme.value = value
  document.documentElement.dataset.theme = value
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.content = COLORS[value]
}

// Пока пользователь не выбрал тему сам, следуем за системой.
media?.addEventListener?.('change', (event) => {
  if (!stored()) apply(event.matches ? 'dark' : 'light')
})

/**
 * Переключает тему; новая раскрывается кругом из точки клика (View Transitions).
 * Без поддержки API или при «меньше движения» — мгновенно.
 */
export async function toggleTheme(event) {
  const next = theme.value === 'dark' ? 'light' : 'dark'
  try {
    localStorage.setItem(THEME_KEY, next)
  } catch {
    // Без localStorage тема живёт до перезагрузки.
  }
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  if (!document.startViewTransition || reduce) {
    apply(next)
    return
  }
  const rect = event?.currentTarget?.getBoundingClientRect?.()
  const x = rect ? rect.left + rect.width / 2 : innerWidth / 2
  const y = rect ? rect.top + rect.height / 2 : 0
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y))
  const transition = document.startViewTransition(() => apply(next))
  try {
    await transition.ready
  } catch {
    // Переход пропущен (вкладка скрыта, повторный клик) — тема уже применена, просто без круга.
    return
  }
  document.documentElement.animate(
    { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
    { duration: 550, easing: 'cubic-bezier(.4, 0, .2, 1)', pseudoElement: '::view-transition-new(root)' },
  )
}

export function useTheme() {
  return { theme, isDark: computed(() => theme.value === 'dark'), toggleTheme }
}
