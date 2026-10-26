<template>
  <Teleport to="body">
    <div v-if="open" class="modal" :class="{ 'modal--top': top }" @mousedown.self="close">
      <div class="modal__panel" :class="{ 'modal__panel--wide': wide }" role="dialog" aria-modal="true" :aria-label="title">
        <div class="modal__head">
          <h2>{{ title }}</h2>
          <button class="modal__close" type="button" aria-label="Закрыть" @click="close">×</button>
        </div>
        <div class="modal__body">
          <slot />
        </div>
        <div v-if="$slots.footer" class="modal__footer">
          <slot name="footer" />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { nextTick, onBeforeUnmount, watch } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  wide: { type: Boolean, default: false },
  // Пока идёт запрос, окно не закрывается — иначе результат потеряется.
  locked: { type: Boolean, default: false },
  // Поверх других окон (подтверждение действия внутри открытого окна).
  top: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

function close() {
  if (!props.locked) emit('close')
}

function onKey(event) {
  if (event.key !== 'Escape') return
  // Esc закрывает только верхнее окно.
  if (!props.top && document.querySelector('.modal--top')) return
  close()
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      window.addEventListener('keydown', onKey)
      document.body.classList.add('has-modal')
    } else {
      window.removeEventListener('keydown', onKey)
      releaseBody()
    }
  },
  { immediate: true },
)

// Прокрутку страницы возвращаем, только когда закрылось последнее окно.
function releaseBody() {
  nextTick(() => {
    if (!document.querySelector('.modal')) document.body.classList.remove('has-modal')
  })
}

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  releaseBody()
})
</script>
