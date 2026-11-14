import { isPhone } from '../composables/useViewport'

/**
 * v-cards — на таблице с классом .tcards: на телефоне строка — карточка (вёрстка в ui.css), а директива
 * отвечает за нажатия.
 * Нажатие на карточку открывает остальные столбцы — то же, что кнопка со стрелкой (UiRowMore).
 * Долгое нажатие отмечает строку флажком из .check-col: появляется выбор (data-picking на таблице),
 * и дальше короткое нажатие на карточку ставит и снимает флажок. Действия с выбранными страница
 * показывает сама — в строке над нижней панелью.
 * Шире 640 px директива ничего не делает.
 */
const HOLD_MS = 450
const MOVE_PX = 10
// По этим элементам нажатие делает своё дело — карточку оно не открывает и не выбирает.
const CONTROLS = 'a, button, input, select, textarea, label, summary, [role="menu"], [role="listbox"], [contenteditable]'

function rowOf(table, target) {
  const row = target instanceof Element ? target.closest('tr') : null
  return row && row.parentElement?.tagName === 'TBODY' && row.closest('table') === table ? row : null
}

function boxOf(row) {
  return row.querySelector(':scope > .check-col input[type="checkbox"]')
}

function syncPicking(table) {
  table.toggleAttribute('data-picking', !!table.querySelector(':scope > tbody > tr > .check-col input:checked'))
}

export const vCards = {
  mounted(table) {
    const state = { timer: 0, x: 0, y: 0, held: false }
    table._cards = state

    const cancel = () => {
      clearTimeout(state.timer)
      state.timer = 0
    }

    state.down = (event) => {
      cancel()
      state.held = false
      if (!isPhone() || (event.pointerType === 'mouse' && event.button !== 0)) return
      const row = rowOf(table, event.target)
      const box = row && boxOf(row)
      if (!box || box.disabled || event.target.closest(CONTROLS)) return
      state.x = event.clientX
      state.y = event.clientY
      state.timer = setTimeout(() => {
        state.timer = 0
        box.click()
        // Палец ещё на экране: нажатие, которое придёт после отпускания, уже отработано.
        state.held = true
        navigator.vibrate?.(10)
      }, HOLD_MS)
    }
    // Палец поехал — это прокрутка страницы, а не выбор.
    state.move = (event) => {
      if (state.timer && Math.hypot(event.clientX - state.x, event.clientY - state.y) > MOVE_PX) cancel()
    }
    state.cancel = cancel
    // Системное меню по долгому нажатию (выделение текста, «Открыть ссылку») выбору мешает.
    state.menu = (event) => {
      if (state.timer || state.held) event.preventDefault()
    }
    // Нажатие сразу после долгого — гасим до обработчиков строки.
    state.swallow = (event) => {
      if (!state.held) return
      state.held = false
      event.preventDefault()
      event.stopPropagation()
    }
    state.click = (event) => {
      if (!isPhone()) return
      const row = rowOf(table, event.target)
      if (!row || event.target.closest(CONTROLS)) return
      if (table.hasAttribute('data-picking')) {
        const box = boxOf(row)
        if (box && !box.disabled) box.click()
        return
      }
      row.querySelector(':scope > .tc-toggle button')?.click()
    }

    table.addEventListener('pointerdown', state.down)
    table.addEventListener('pointermove', state.move)
    table.addEventListener('pointerup', state.cancel)
    table.addEventListener('pointercancel', state.cancel)
    table.addEventListener('pointerleave', state.cancel)
    table.addEventListener('contextmenu', state.menu)
    table.addEventListener('click', state.swallow, true)
    table.addEventListener('click', state.click)
    syncPicking(table)
  },
  // Флажки строк перерисовывает страница — после каждой перерисовки смотрим, есть ли отмеченные.
  updated(table) {
    syncPicking(table)
  },
  unmounted(table) {
    const state = table._cards
    if (!state) return
    state.cancel()
    table.removeEventListener('pointerdown', state.down)
    table.removeEventListener('pointermove', state.move)
    table.removeEventListener('pointerup', state.cancel)
    table.removeEventListener('pointercancel', state.cancel)
    table.removeEventListener('pointerleave', state.cancel)
    table.removeEventListener('contextmenu', state.menu)
    table.removeEventListener('click', state.swallow, true)
    table.removeEventListener('click', state.click)
    delete table._cards
  },
}
