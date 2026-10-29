<template>
  <Modal :open="!!current" :title="current?.title || ''" top @close="answer(false)">
    <p v-if="current?.message" class="confirm-text">{{ current.message }}</p>
    <form v-if="current?.input" class="confirm-form" @submit.prevent="submit">
      <div class="field">
        <label :for="inputId">{{ current.input.label }}</label>
        <input
          :id="inputId"
          ref="inputEl"
          v-model="current.input.value"
          :type="current.input.type === 'number' ? 'text' : current.input.type"
          :inputmode="current.input.type === 'number' ? 'decimal' : undefined"
          :placeholder="current.input.placeholder"
          autocomplete="off"
        />
      </div>
    </form>
    <template #footer>
      <button class="btn btn--ghost" type="button" @click="answer(false)">{{ current?.cancelText }}</button>
      <button
        ref="okButton"
        class="btn"
        :class="current?.danger ? 'btn--danger-solid' : 'btn--accent'"
        type="button"
        :disabled="blocked"
        @click="submit"
      >
        {{ current?.confirmText }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import Modal from './Modal.vue'
import { useConfirm } from '../../composables/useConfirm'

const { current } = useConfirm()
const okButton = ref(null)
const inputEl = ref(null)
const inputId = 'confirm-input'

const blocked = computed(() => !!current.value?.input?.required && !String(current.value.input.value).trim())

function answer(value) {
  current.value?.resolve(value)
}

function submit() {
  if (!blocked.value) answer(true)
}

watch(current, async (value) => {
  if (!value) return
  await nextTick()
  if (value.input) {
    inputEl.value?.focus()
    inputEl.value?.select()
  } else {
    okButton.value?.focus()
  }
})
</script>
