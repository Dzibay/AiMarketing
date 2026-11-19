<template>
  <p v-if="shown" class="notice defaults-ask" role="status">
    <span>Сделать так по умолчанию для новых проектов?</span>
    <span class="defaults-ask__acts">
      <button class="btn btn--ghost btn--sm" type="button" :disabled="busy" @click="yes">Да</button>
      <button class="btn btn--ghost btn--sm" type="button" :disabled="busy" @click="markAsked(kind)">Нет</button>
    </span>
  </p>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { defaultsApi, errorText } from '../api/web'
import { markAsked, useDefaultsAsk } from '../composables/useDefaultsAsk'
import { pushToast } from '../composables/useToasts'

/** Вопрос под сохранённой настройкой проекта; виды — как в offerDefaults. Ответ запоминается в браузере. */
const props = defineProps({ kind: { type: String, required: true } })

const { pending } = useDefaultsAsk()
const busy = ref(false)
const shown = computed(() => pending.value?.kind === props.kind)
// Ушли со страницы, не ответив, — вопрос не висит в другом проекте; задастся при следующем сохранении.
onBeforeUnmount(() => {
  if (shown.value) pending.value = null
})

async function yes() {
  const { wsId, kind } = pending.value
  busy.value = true
  try {
    await defaultsApi.asDefaults(wsId, [kind])
    markAsked(kind)
    pushToast('Готово — так настроятся новые проекты. Изменить: Настройки → «Для новых проектов»', 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.defaults-ask {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
  margin-top: 12px;
}
.defaults-ask__acts {
  display: flex;
  gap: 8px;
}
</style>
