<template>
  <UiMenu v-if="problem.key" size="sm" :label="label">
    <UiMenuItem :icon="Clock" :disabled="busy" @click="snooze('days')">Скрыть на 7 дней</UiMenuItem>
    <UiMenuItem :icon="CircleCheck" :disabled="busy" hint="Покажем снова, если станет хуже" @click="snooze('intended')">
      Так задумано — не показывать, пока не изменится
    </UiMenuItem>
    <UiMenuItem v-if="scope" :icon="EyeOff" :disabled="busy" :hint="scopeName" @click="snooze('type')">
      Скрыть такой тип для этого {{ scope.level === 'client' ? 'клиента' : 'проекта' }}
    </UiMenuItem>
  </UiMenu>
</template>

<script setup>
import { computed, ref } from 'vue'
import { CircleCheck, Clock, EyeOff } from '@lucide/vue'
import UiMenu from './ui/UiMenu.vue'
import UiMenuItem from './ui/UiMenuItem.vue'
import { errorText, webApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { niceQuotes } from '../utils/format'

/**
 * «⋯» у сигнала: скрыть на 7 дней, «так задумано» (вернётся, если станет хуже) или весь такой тип
 * для клиента (папки) либо проекта. Скрытие хранится на сервере — пропадает из счётчиков, дайджеста,
 * колокольчика и Telegram. key и type_scope приходят с проблемой из сводки. Скрытый тип прячет только
 * то, что не серьёзнее скрытого: кончившиеся деньги или отказ модерации всё равно покажем.
 */
const props = defineProps({
  problem: { type: Object, required: true },
})
const emit = defineEmits(['snoozed'])

const busy = ref(false)
const scope = computed(() => props.problem.type_scope || null)
const scopeName = computed(() => niceQuotes(scope.value?.name || ''))
const label = computed(() => `Скрыть сигнал «${props.problem.title}»`)

const DONE = {
  days: 'Скрыли на 7 дней',
  intended: 'Скрыли — покажем снова, если станет хуже',
  type: 'Такие сигналы скрыли — покажем снова, если станет хуже',
}

async function snooze(mode) {
  if (busy.value) return
  busy.value = true
  try {
    await webApi.snoozeProblem(props.problem.key, mode)
    emit('snoozed', {
      key: props.problem.key,
      mode,
      kind: props.problem.kind,
      scope: scope.value?.key || null,
      severity: props.problem.severity,
    })
    pushToast(`${DONE[mode]}. Вернуть можно внизу, в строке «Скрыто»`, 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}
</script>
