/**
 * v-scroll-x — на обёртке таблицы (.table-scroll). Своей прокрутки у таблицы нет, пока она помещается:
 * страница скроллится целиком, строка заголовков липнет под верхнюю панель (.is-fit). Не помещается —
 * обёртка прокручивается вбок (.is-scroll): галочка и название стоят на месте, а после сдвига
 * (.is-scrolled) у края названия появляется тень.
 * Без директивы обёртка просто прокручивается вбок, как раньше, — таблица не вылезает за страницу.
 */
function sync(el) {
  const over = el.scrollWidth > el.clientWidth + 1
  el.classList.toggle('is-fit', !over)
  el.classList.toggle('is-scroll', over)
  el.classList.toggle('is-scrolled', over && el.scrollLeft > 0)
  // Название липнет сразу за галочкой: CSS не знает её ширину с отступами, меряем.
  const check = over && el.querySelector('.check-col')
  if (check) el.style.setProperty('--check-w', `${check.offsetWidth}px`)
}

// Ширина таблицы меняется с данными, а не только с окном: следим и за обёрткой, и за самой таблицей.
function watchChild(el) {
  const state = el._scrollX
  const child = el.firstElementChild
  if (child === state.child) return
  if (state.child) state.ro.unobserve(state.child)
  if (child) state.ro.observe(child)
  state.child = child
}

export const vScrollX = {
  mounted(el) {
    const state = { child: null, ro: null, onScroll: null }
    el._scrollX = state
    state.onScroll = () => el.classList.toggle('is-scrolled', el.classList.contains('is-scroll') && el.scrollLeft > 0)
    el.addEventListener('scroll', state.onScroll, { passive: true })
    if (typeof ResizeObserver !== 'undefined') {
      state.ro = new ResizeObserver(() => sync(el))
      state.ro.observe(el)
      watchChild(el)
    }
    sync(el)
  },
  // Привязка :class у той же обёртки (is-loading) перезаписывает className — состояние ставим заново.
  updated(el) {
    if (el._scrollX?.ro) watchChild(el)
    sync(el)
  },
  unmounted(el) {
    const state = el._scrollX
    if (!state) return
    state.ro?.disconnect()
    el.removeEventListener('scroll', state.onScroll)
    delete el._scrollX
  },
}
