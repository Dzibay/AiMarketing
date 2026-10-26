<template>
  <Modal :open="!!current" :title="current?.title || ''" top @close="answer(false)">
    <p class="confirm-text">{{ current?.message }}</p>
    <template #footer>
      <button class="btn btn--ghost" type="button" @click="answer(false)">{{ current?.cancelText }}</button>
      <button
        ref="okButton"
        class="btn"
        :class="current?.danger ? 'btn--danger-solid' : 'btn--accent'"
        type="button"
        @click="answer(true)"
      >
        {{ current?.confirmText }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { nextTick, ref, watch } from 'vue'
import Modal from './Modal.vue'
import { useConfirm } from '../../composables/useConfirm'

const { current } = useConfirm()
const okButton = ref(null)

function answer(value) {
  current.value?.resolve(value)
}

watch(current, async (value) => {
  if (!value) return
  await nextTick()
  okButton.value?.focus()
})
</script>
