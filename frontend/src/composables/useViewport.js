import { computed, ref } from 'vue'

/**
 * Размер экрана для компонентов — пороги те же, что в стилях.
 * phone — до 640 px: меню, списки и даты открываются шторкой снизу.
 * narrow — до 900 px: бокового меню нет, разделы — в нижней панели.
 * typing — открыта экранная клавиатура: нижняя панель прячется, шторка встаёт над клавиатурой
 * (её высота — в переменной --kb на <html>).
 */
export const PHONE = '(max-width: 640px)'
export const NARROW = '(max-width: 900px)'

function media(query) {
  const list = window.matchMedia(query)
  const state = ref(list.matches)
  list.addEventListener('change', (event) => (state.value = event.matches))
  return state
}

const phone = media(PHONE)
const narrow = media(NARROW)
const coarse = media('(pointer: coarse)')

/** Сейчас телефон — для кода, который решает один раз в момент открытия панели. */
export const isPhone = () => window.matchMedia(PHONE).matches

// --- Экранная клавиатура ---

// Меньше — это панели браузера (адресная строка, жесты), а не клавиатура.
const KEYBOARD_MIN = 120

const inset = ref(0)
const field = ref(false)

function isTextField(el) {
  if (!(el instanceof HTMLElement)) return false
  if (el.isContentEditable || el.tagName === 'TEXTAREA') return true
  return el.tagName === 'INPUT' && !/^(checkbox|radio|button|submit|reset|file|range|color|image)$/.test(el.type)
}

function measure() {
  const view = window.visualViewport
  // При увеличении щипком видимая область тоже меньше окна — клавиатура тут ни при чём.
  const hidden = !view || view.scale > 1.01 ? 0 : Math.round(window.innerHeight - view.height - view.offsetTop)
  inset.value = hidden > KEYBOARD_MIN ? hidden : 0
  document.documentElement.style.setProperty('--kb', `${inset.value}px`)
}

window.visualViewport?.addEventListener('resize', measure)
window.visualViewport?.addEventListener('scroll', measure)
// Там, где клавиатура сжимает само окно (старый Android), её видно только по фокусу в поле.
document.addEventListener('focusin', (event) => (field.value = isTextField(event.target)))
document.addEventListener('focusout', () => (field.value = false))
measure()

const typing = computed(() => inset.value > 0 || (coarse.value && field.value))

export function useViewport() {
  return { phone, narrow, typing }
}
