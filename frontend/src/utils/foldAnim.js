/**
 * Свёртка карточки после «Выполнить»: высоту большой карточки меряем до перерисовки, а когда на её месте
 * встала строка с галочкой — плавно сжимаем от старой высоты к новой. При prefers-reduced-motion — без движения.
 */

export function reducedMotion() {
  return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

/** Высота элемента сейчас, до перерисовки; 0 — элемента нет. */
export function heightOf(selector) {
  return document.querySelector(selector)?.offsetHeight || 0
}

/** Сжать элемент от высоты from до его собственной. Вызывать после перерисовки (nextTick). */
export function foldFrom(selector, from) {
  if (!from || reducedMotion()) return
  const el = document.querySelector(selector)
  if (!el?.animate) return
  const to = el.offsetHeight
  if (to >= from) return
  const overflow = el.style.overflow
  el.style.overflow = 'hidden'
  const anim = el.animate([{ height: `${from}px` }, { height: `${to}px` }], {
    duration: 320,
    easing: 'cubic-bezier(0.45, 0, 0.25, 1)',
  })
  const restore = () => (el.style.overflow = overflow)
  anim.onfinish = restore
  anim.oncancel = restore
}
