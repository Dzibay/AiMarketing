import { reactive } from 'vue'

/**
 * Одна общая подсказка на всё приложение (её рисует TooltipHost): у какого элемента, что написано, где стоит.
 * Показывают её директива v-tip и значок «?» (TermHint).
 */
export const TIP_ID = 'ui-tip'

export const tip = reactive({
  el: null,
  title: '',
  text: '',
  side: 'top',
  // Закреплённая нажатием подсказка («?» на телефоне) не закрывается, пока не нажмут мимо.
  pinned: false,
})

// Сразу после закрытия следующая подсказка открывается без задержки: ведут мышью по ряду кнопок.
let warmUntil = 0
export const isWarm = () => Date.now() < warmUntil

export function showTip(el, { text, title = '', side = 'top', pinned = false, describe = true }) {
  if (tip.el && tip.el !== el) hideTip(tip.el)
  Object.assign(tip, { el, text, title, side, pinned })
  if (describe) {
    const ids = (el.getAttribute('aria-describedby') || '').split(' ').filter(Boolean)
    if (!ids.includes(TIP_ID)) el.setAttribute('aria-describedby', [...ids, TIP_ID].join(' '))
  }
}

export function hideTip(el) {
  if (!tip.el || (el && tip.el !== el)) return
  const owner = tip.el
  const ids = (owner.getAttribute('aria-describedby') || '').split(' ').filter((id) => id && id !== TIP_ID)
  if (ids.length) owner.setAttribute('aria-describedby', ids.join(' '))
  else owner.removeAttribute('aria-describedby')
  Object.assign(tip, { el: null, pinned: false })
  warmUntil = Date.now() + 300
}
