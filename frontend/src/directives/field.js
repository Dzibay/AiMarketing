let seq = 0

// [data-field-control] — кнопка собственного выпадающего списка (UiSelect).
const CONTROL =
  'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), select, textarea, [data-field-control]'

/** Связывает подпись блока с первым полем внутри (for/id): клик по подписи ставит фокус, скринридер читает название поля. */
function link(el) {
  const label = el.querySelector(':scope > label')
  if (!label || label.control) return
  const control = el.querySelector(CONTROL)
  if (!control) return
  if (!control.id) control.id = `field-${++seq}`
  label.htmlFor = control.id
  // У кнопки списка или даты подпись заменила бы текст кнопки, и диктор не услышал бы выбранное.
  // Имя — «подпись + значение»; for остаётся, чтобы клик по подписи ставил фокус.
  const value = control.matches('[data-field-control]') && control.querySelector('.ui-select__value')
  if (value && !control.hasAttribute('aria-label') && !control.hasAttribute('aria-labelledby')) {
    if (!label.id) label.id = `${control.id}-label`
    if (!value.id) value.id = `${control.id}-value`
    control.setAttribute('aria-labelledby', `${label.id} ${value.id}`)
  }
}

/** v-field на блоке .field — поля внутри рендерятся по условию, поэтому связь проверяется и после обновлений. */
export const vField = { mounted: link, updated: link }
