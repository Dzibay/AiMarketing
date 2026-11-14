<template>
  <Teleport v-if="$slots.back" defer to="#dhead-back">
    <slot name="back" />
  </Teleport>
  <Teleport v-if="description || $slots.description" defer to="#dhead-desc">
    <slot name="description">
      <div class="dhead__clip">
        <span ref="clipEl" class="dhead__clip-t">{{ description }}</span>
        <button v-if="clipped" class="ui-more__toggle" type="button" aria-haspopup="dialog" @click="descOpen = true">
          Подробнее
        </button>
      </div>
      <UiSheet :open="descOpen" title="Подробнее" @close="descOpen = false">
        <div class="ui-more__sheet">
          <p>{{ description }}</p>
        </div>
      </UiSheet>
    </slot>
  </Teleport>
  <Teleport v-if="$slots.actions || (!phone && $slots.more)" defer to="#dhead-acts">
    <slot v-if="!phone" name="more" :sheet="false" />
    <slot name="actions" />
  </Teleport>
  <Teleport v-if="phone && $slots.more" defer to="#dhead-more">
    <button
      class="dicon dhead__dots"
      type="button"
      aria-label="Ещё действия"
      aria-haspopup="dialog"
      @click="moreOpen = true"
    >
      <Ellipsis aria-hidden="true" />
    </button>
    <UiSheet :open="moreOpen" title="Действия" @close="moreOpen = false">
      <div class="dhead__sheet" @click="onSheetClick">
        <slot name="more" :sheet="true" />
      </div>
    </UiSheet>
  </Teleport>
</template>

<script setup>
import { nextTick, onBeforeUnmount, ref, watch, watchEffect } from 'vue'
import { Ellipsis } from '@lucide/vue'
import UiSheet from './UiSheet.vue'
import { clearPageTitle, setPageTitle } from '../../composables/usePageTitle'
import { useViewport } from '../../composables/useViewport'

/**
 * Шапка страницы. Заголовок h1 — один, его пишет оболочка (AppShell) по пункту меню; страница добавляет к нему
 * строку описания и свои действия справа — период, «Обновить», главную кнопку. Вторых крупных заголовков
 * внутри страницы не делаем. Слот back — ссылка «← назад» строкой над заголовком (карточка внутри раздела).
 *
 * Действий два слота: actions — главное (кнопка «Новая кампания», выключатель показов), more — второстепенные
 * (период, «Обновить»). На широком экране они стоят в одном ряду, more — первым. На телефоне more уходит
 * за кнопку «⋯» у заголовка: она открывает шторку, слот получает sheet=true (можно подписать группу),
 * а нажатие на кнопку или ссылку в шторке её закрывает.
 *
 * Описание текстом (description) на телефоне — одна строка: не поместилось — многоточие и «Подробнее»,
 * полный текст в шторке. Описание слотом страница устраивает сама (UiMore делает то же).
 */
const props = defineProps({
  description: { type: String, default: '' },
  // Свой заголовок вместо названия пункта меню — только там, где он важнее (приветствие на главной).
  title: { type: String, default: '' },
})

const owner = Symbol('page-title')
watchEffect(() => setPageTitle(owner, props.title))
onBeforeUnmount(() => clearPageTitle(owner))

const { phone } = useViewport()

// --- «⋯»: второстепенные действия в шторке ---

const moreOpen = ref(false)

function onSheetClick(event) {
  const control = event.target instanceof Element ? event.target.closest('button, a') : null
  // Кнопка, которая сама открывает меню или список, шторку не закрывает — иначе закрылось бы и её окно.
  if (control && !control.disabled && !control.hasAttribute('aria-haspopup')) moreOpen.value = false
}

// --- Описание в одну строку ---

const clipEl = ref(null)
const clipped = ref(false)
const descOpen = ref(false)
let sizes = null
let clipFrame = 0

function measureClip() {
  const el = clipEl.value
  // Проверяем только на телефоне: на широком экране строка не обрезается.
  clipped.value = !!el && phone.value && el.scrollWidth > el.clientWidth + 1
}
// «Подробнее» сужает саму строку — из колбэка ResizeObserver замер переносим на следующий кадр,
// иначе браузер ругается на цикл.
function measureClipLater() {
  cancelAnimationFrame(clipFrame)
  clipFrame = requestAnimationFrame(measureClip)
}

watch(
  clipEl,
  (el) => {
    sizes?.disconnect()
    sizes = null
    if (!el) return
    sizes = new ResizeObserver(measureClipLater)
    sizes.observe(el)
    sizes.observe(el.parentElement)
  },
  { flush: 'post' },
)
watch([phone, () => props.description], () => nextTick(measureClip), { flush: 'post' })

// Экран стал шире телефона — шторки тут не открываются.
watch(phone, (on) => {
  if (on) return
  moreOpen.value = false
  descOpen.value = false
})

onBeforeUnmount(() => {
  sizes?.disconnect()
  cancelAnimationFrame(clipFrame)
})
</script>
