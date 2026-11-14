import { onBeforeUnmount, reactive, watch } from 'vue'

/**
 * Лента, которая листается вбок: вкладки страницы, плитки показателей на телефоне.
 * useScrollStrip(target) → { strip, measure }.
 * strip.before / strip.after — слева или справа есть скрытое (по ним край затеняется).
 * strip.pages / strip.page — сколько «экранов» в ленте и какой сейчас виден (точки под лентой).
 * Пересчёт — при прокрутке, смене размера и состава ленты; measure() — вручную, если нужно сразу.
 */
export function useScrollStrip(target) {
  const strip = reactive({ before: false, after: false, pages: 1, page: 0 })
  let el = null
  let sizes = null
  let nodes = null
  let frame = 0

  function measure() {
    if (!el) return
    const max = el.scrollWidth - el.clientWidth
    strip.before = el.scrollLeft > 1
    strip.after = el.scrollLeft < max - 1
    const first = el.firstElementChild
    const second = first?.nextElementSibling
    if (max <= 1 || !second) {
      strip.pages = 1
      strip.page = 0
      return
    }
    // Шаг — элемент вместе с промежутком; в экран помещается целое число элементов.
    const step = second.getBoundingClientRect().left - first.getBoundingClientRect().left
    if (step <= 0) return
    const style = getComputedStyle(el)
    const inner = el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
    const gap = step - first.getBoundingClientRect().width
    const perPage = Math.max(1, Math.floor((inner + gap + 1) / step))
    strip.pages = Math.ceil(el.children.length / perPage)
    strip.page = strip.after ? Math.min(strip.pages - 1, Math.round(el.scrollLeft / (step * perPage))) : strip.pages - 1
  }

  // Замер меняет вёрстку (точки, затенение) — из колбэка ResizeObserver переносим его на следующий кадр.
  function measureLater() {
    cancelAnimationFrame(frame)
    frame = requestAnimationFrame(measure)
  }

  function watchChildren() {
    sizes.disconnect()
    sizes.observe(el)
    for (const child of el.children) sizes.observe(child)
    measure()
  }

  function bind(next) {
    if (el) {
      el.removeEventListener('scroll', measure)
      sizes.disconnect()
      nodes.disconnect()
      cancelAnimationFrame(frame)
    }
    el = next || null
    if (!el) {
      Object.assign(strip, { before: false, after: false, pages: 1, page: 0 })
      return
    }
    el.addEventListener('scroll', measure, { passive: true })
    sizes = new ResizeObserver(measureLater)
    nodes = new MutationObserver(watchChildren)
    nodes.observe(el, { childList: true })
    watchChildren()
  }

  watch(target, bind, { immediate: true, flush: 'post' })
  onBeforeUnmount(() => bind(null))

  return { strip, measure }
}

/** Прокрутить ленту так, чтобы элемент был виден целиком (с отступом от затенённого края); без анимации. */
export function revealInStrip(el, item, margin = 28) {
  if (!el || !item) return
  const box = el.getBoundingClientRect()
  const at = item.getBoundingClientRect()
  if (at.left >= box.left + margin && at.right <= box.right - margin) return
  const left = at.left - box.left + el.scrollLeft - (el.clientWidth - at.width) / 2
  el.scrollLeft = Math.max(0, Math.min(left, el.scrollWidth - el.clientWidth))
}
