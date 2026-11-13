import { hideTip, isWarm, showTip, tip } from '../composables/useTooltip'

const DELAY = 400

/**
 * v-tip="'Текст'" — своя подсказка вместо title: тот же вид в обеих темах, перенос строк, до 280 px.
 * Мышь — через 400 мс, фокус с клавиатуры — сразу, Esc закрывает, на телефоне не показывается.
 * Пустое значение — подсказки нет.
 * v-tip.truncate — только когда текст элемента обрезан многоточием (названия кампаний, проектов, чатов).
 * v-tip.right — справа от элемента (свёрнутое боковое меню).
 * Элементу без названия (кнопка-иконка без aria-label) текст подсказки становится aria-label.
 */
// Обрезан сам элемент или текст внутри него (строка меню: многоточие у вложенного span).
function clipped(el) {
  const over = (n) => n.scrollWidth > n.clientWidth + 1 || n.scrollHeight > n.clientHeight + 1
  if (over(el)) return true
  return [...el.querySelectorAll('*')].some((n) => getComputedStyle(n).overflow !== 'visible' && over(n))
}

function open(el) {
  const state = el._tip
  if (!state?.text) return
  if (state.truncate && !clipped(el)) return
  // Текст, который и так читается (название кнопки, сам обрезанный текст), второй раз не озвучиваем.
  const same = el.getAttribute('aria-label') === state.text || el.textContent.trim() === state.text
  showTip(el, { text: state.text, side: state.side, describe: !state.truncate && !same })
}

function name(el, text) {
  const interactive = el.matches('button, a, [role="button"], [tabindex]')
  const named = el.hasAttribute('aria-labelledby') || el.textContent.trim()
  if (!interactive || named) return
  if (!el.hasAttribute('aria-label') || el._tip?.ownLabel) {
    if (text) el.setAttribute('aria-label', text)
    else el.removeAttribute('aria-label')
    return true
  }
  return false
}

function setup(el, binding) {
  const text = binding.value == null || binding.value === false ? '' : String(binding.value)
  const ownLabel = name(el, text)
  el._tip = {
    ...el._tip,
    text,
    truncate: !!binding.modifiers.truncate,
    side: binding.modifiers.right ? 'right' : 'top',
    ownLabel: el._tip?.ownLabel || ownLabel,
  }
  if (tip.el === el) {
    if (text) open(el)
    else hideTip(el)
  }
}

const handlers = {
  pointerenter(event) {
    if (event.pointerType !== 'mouse') return
    const el = event.currentTarget
    clearTimeout(el._tipTimer)
    el._tipTimer = setTimeout(() => open(el), isWarm() ? 0 : DELAY)
  },
  pointerleave(event) {
    const el = event.currentTarget
    clearTimeout(el._tipTimer)
    hideTip(el)
  },
  pointerdown(event) {
    const el = event.currentTarget
    clearTimeout(el._tipTimer)
    hideTip(el)
  },
  focusin(event) {
    // Только фокус с клавиатуры: после нажатия мышью подсказка не нужна.
    if (event.target.matches(':focus-visible')) open(event.currentTarget)
  },
  focusout(event) {
    hideTip(event.currentTarget)
  },
}

export const vTip = {
  mounted(el, binding) {
    setup(el, binding)
    for (const [type, fn] of Object.entries(handlers)) el.addEventListener(type, fn)
  },
  updated(el, binding) {
    if (binding.value !== binding.oldValue) setup(el, binding)
  },
  beforeUnmount(el) {
    clearTimeout(el._tipTimer)
    for (const [type, fn] of Object.entries(handlers)) el.removeEventListener(type, fn)
    hideTip(el)
  },
}
