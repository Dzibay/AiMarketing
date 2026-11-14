/**
 * Поведение панели, открытой шторкой снизу (телефон). Общее для UiSheet и всплывающих панелей usePopover:
 * под шторкой — затемнение, нажатие на него и Esc закрывают; остальная страница недоступна (inert: фокус и диктор
 * остаются в шторке) и не прокручивается; шторку можно смахнуть вниз. Шторки складываются стопкой
 * («Ещё» → выбор проекта): закрывается и слушает Esc только верхняя.
 *
 * sheetLayer(close) → { attach(el), detach() }.
 * attach — когда панель уже в документе. detach — до возврата фокуса на кнопку: пока шторка открыта, кнопка под inert.
 * Панели нужен class="is-sheet"; :style у неё — объект (не null): слой сам пишет в style порядок слоёв и сдвиг жеста.
 */
const stack = []

// Слои: затемнение и панель первой шторки — как у обычной всплывающей панели, следующая шторка — выше.
const Z_BASE = 74
// Сколько тянуть вниз, чтобы шторка закрылась, и с какого сдвига движение считается жестом.
const SWIPE_CLOSE = 90
const SWIPE_START = 8
const SLIDE_MS = 180

export function sheetLayer(close) {
  let el = null
  let scrim = null
  let frozen = []
  let touch = null
  let timer = 0

  const layer = { attach, detach }

  function isTop() {
    return stack.at(-1) === layer
  }

  function onKey(event) {
    if (event.key !== 'Escape' || !isTop()) return
    // Фокус в панели, открытой поверх шторки (список проектов на планшете), — Esc закрывает её, а не шторку.
    const at = event.target
    if (at instanceof Element && at !== document.body && at !== document.documentElement && !el.contains(at)) return
    // Esc закрывает только шторку — не окно под ней и не всё сразу.
    event.preventDefault()
    event.stopPropagation()
    close()
  }

  // Список внутри шторки прокручен — жест вниз листает его, а не закрывает шторку.
  function scrolled(target) {
    for (let node = target; node instanceof Element; node = node.parentElement) {
      if (node.scrollTop > 0) return true
      if (node === el) break
    }
    return false
  }

  function onTouchStart(event) {
    touch = null
    if (event.touches.length !== 1 || !isTop() || scrolled(event.target)) return
    const { clientX, clientY } = event.touches[0]
    touch = { x: clientX, y: clientY, dy: 0, drag: false }
  }

  function onTouchMove(event) {
    if (!touch) return
    const dx = event.touches[0].clientX - touch.x
    const dy = event.touches[0].clientY - touch.y
    if (!touch.drag) {
      // Вверх или вбок — это прокрутка и горизонтальные жесты, шторку не трогаем.
      if (dy < -SWIPE_START || Math.abs(dx) > Math.max(SWIPE_START, dy)) touch = null
      if (!touch || dy < SWIPE_START) return
      touch.drag = true
      el.style.transition = 'none'
    }
    event.preventDefault()
    touch.dy = Math.max(0, dy)
    el.style.transform = `translateY(${touch.dy}px)`
  }

  function onTouchEnd() {
    if (!touch?.drag) {
      touch = null
      return
    }
    const away = touch.dy > Math.min(SWIPE_CLOSE, el.offsetHeight / 3)
    touch = null
    el.style.transition = `transform ${SLIDE_MS}ms ease`
    el.style.transform = away ? 'translateY(100%)' : ''
    if (away) timer = window.setTimeout(close, SLIDE_MS - 20)
  }

  function listen(on) {
    const method = on ? 'addEventListener' : 'removeEventListener'
    document[method]('keydown', onKey, true)
    el[method]('touchstart', onTouchStart, { passive: true })
    el[method]('touchmove', onTouchMove, { passive: false })
    el[method]('touchend', onTouchEnd)
    el[method]('touchcancel', onTouchEnd)
  }

  function attach(target) {
    if (el || !target?.parentNode) return
    el = target
    const z = Z_BASE + stack.length * 2
    scrim = document.createElement('div')
    scrim.className = 'ui-scrim'
    scrim.style.zIndex = String(z)
    // click, а не pointerdown: иначе то же нажатие «провалилось» бы в страницу под исчезнувшим затемнением.
    scrim.addEventListener('click', close)
    el.style.zIndex = String(z + 1)
    el.parentNode.insertBefore(scrim, el)
    frozen = [...document.body.children].filter(
      (node) => node !== el && node !== scrim && !node.inert && !/^(SCRIPT|STYLE|LINK)$/.test(node.tagName),
    )
    for (const node of frozen) node.inert = true
    if (!stack.length) document.body.classList.add('has-sheet')
    stack.push(layer)
    listen(true)
  }

  function detach() {
    if (!el) return
    window.clearTimeout(timer)
    listen(false)
    scrim.remove()
    for (const node of frozen) node.inert = false
    stack.splice(stack.indexOf(layer), 1)
    if (!stack.length) document.body.classList.remove('has-sheet')
    el = null
    scrim = null
    frozen = []
    touch = null
  }

  return layer
}
