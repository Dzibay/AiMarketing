<template>
  <UndoDialog
    :log-id="current?.id ?? null"
    :log-ids="current?.ids ?? null"
    @close="closeUndo"
    @done="onDone"
  />
</template>

<script setup>
import UndoDialog from './UndoDialog.vue'
import { useUndo } from '../composables/useUndo'

// Окно отката для кнопки «Отменить» в сообщениях о результате (useUndo.undoAction): одна запись или
// несколько записей фоновой задачи разом.
const { current, closeUndo } = useUndo()

function onDone(res) {
  current.value?.onDone?.(res)
}
</script>
