let seq = 0

const CONTROL = 'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), select, textarea'

/** Связывает подпись блока с первым полем внутри (for/id): клик по подписи ставит фокус, скринридер читает название поля. */
function link(el) {
  const label = el.querySelector(':scope > label')
  if (!label || label.control) return
  const control = el.querySelector(CONTROL)
  if (!control) return
  if (!control.id) control.id = `field-${++seq}`
  label.htmlFor = control.id
}

/** v-field на блоке .field — поля внутри рендерятся по условию, поэтому связь проверяется и после обновлений. */
export const vField = { mounted: link, updated: link }
