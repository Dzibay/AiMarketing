import { nextTick, onBeforeUnmount, ref } from 'vue'
import { sheetLayer } from './useSheet'
import { isPhone } from './useViewport'

/**
 * Всплывающая панель у кнопки — меню «⋯» и выпадающий список.
 *
 * Панель рисуется в body (Teleport) с position: fixed, поэтому карточки с overflow: hidden её не обрезают.
 * Открывается вниз, а если снизу не хватает места — вверх; в края окна не упирается. Закрывается кликом мимо,
 * прокруткой страницы и сменой размера окна.
 *
 * trigger и panel — ref на элементы. align: 'end' — правым краем к кнопке, 'start' — левым.
 * matchWidth — панель не уже кнопки (выпадающий список).
 * follow — при прокрутке и смене размера окна панель переставляется, а не закрывается. Для кнопки в закреплённой
 * верхней строке: окно «сжимает» экранная клавиатура, и панель с полем поиска иначе закрылась бы
 * от нажатия на это поле.
 *
 * На телефоне (до 640 px) панель открывается шторкой снизу: sheet = true, у кнопки её не ставим (style пустой),
 * затемнение, жест вниз и Esc — в useSheet. Панели нужно :class="{ 'is-sheet': sheet }". Закрытая шторка
 * возвращает фокус на кнопку всегда: пока она открыта, остальная страница недоступна.
 */
export function usePopover(trigger, panel, { align = 'end', gap = 6, matchWidth = false, follow = false, onClose } = {}) {
  const open = ref(false)
  const style = ref({})
  const sheet = ref(false)
  const layer = sheetLayer(() => close())

  function place() {
    const t = trigger.value
    const p = panel.value
    if (!t || !p) return
    const r = t.getBoundingClientRect()
    const vw = document.documentElement.clientWidth
    const vh = window.innerHeight
    const minWidth = matchWidth ? r.width : 0
    const w = Math.max(p.offsetWidth, minWidth)
    const h = p.offsetHeight
    let left = align === 'end' ? r.right - w : r.left
    left = Math.max(8, Math.min(left, vw - w - 8))
    let top = r.bottom + gap
    if (top + h > vh - 8 && r.top - gap - h >= 8) top = r.top - gap - h
    style.value = {
      top: `${Math.round(top)}px`,
      left: `${Math.round(left)}px`,
      ...(minWidth ? { minWidth: `${Math.round(minWidth)}px` } : {}),
    }
  }

  function onPointerDown(event) {
    const target = event.target
    if (trigger.value?.contains(target) || panel.value?.contains(target)) return
    close()
  }

  function onScroll(event) {
    // Прокрутка внутри самой панели (длинный список) её не закрывает.
    if (panel.value?.contains(event.target)) return
    if (follow) place()
    else close()
  }

  function onResize() {
    if (follow) place()
    else close()
  }

  // Шторка: клавиатура и поворот экрана меняют размер окна — закрываем, только если экран стал широким.
  function onSheetResize() {
    if (!isPhone()) close()
  }

  function listen(on) {
    const method = on ? 'addEventListener' : 'removeEventListener'
    document[method]('pointerdown', onPointerDown, true)
    window[method]('scroll', onScroll, true)
    window[method]('resize', onResize)
  }

  async function show() {
    if (open.value) return
    sheet.value = isPhone()
    open.value = true
    if (sheet.value) {
      style.value = {}
      await nextTick()
      layer.attach(panel.value)
      window.addEventListener('resize', onSheetResize)
      return
    }
    // Сначала панель невидима: размеры известны только после отрисовки, а прыгать на глазах ей нельзя.
    style.value = { visibility: 'hidden' }
    await nextTick()
    place()
    listen(true)
  }

  function close({ focus = false } = {}) {
    if (!open.value) return
    open.value = false
    listen(false)
    window.removeEventListener('resize', onSheetResize)
    layer.detach()
    if (focus || sheet.value) trigger.value?.focus({ preventScroll: sheet.value })
    onClose?.()
  }

  function toggle() {
    if (open.value) close()
    else show()
  }

  onBeforeUnmount(() => {
    listen(false)
    window.removeEventListener('resize', onSheetResize)
    layer.detach()
  })

  return { open, style, sheet, show, close, toggle, place }
}
