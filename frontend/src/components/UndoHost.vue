<template>
  <UndoDialog :log-id="current?.id ?? null" @close="closeUndo" @done="onDone" />
</template>

<script setup>
import UndoDialog from './UndoDialog.vue'
import { useUndo } from '../composables/useUndo'

// Окно отката для кнопки «Отменить» в сообщениях о результате (useUndo.undoAction).
const { current, closeUndo } = useUndo()

function onDone(res) {
  current.value?.onDone?.(res)
}
</script>
