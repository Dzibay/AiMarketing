<template>
  <div v-if="freshness" class="fresh">
    <p v-if="!onlyLagging" class="fresh__line">
      <Clock aria-hidden="true" />
      <span>{{ mainText }}</span>
    </p>
    <p v-for="a in lagging" :key="a.id" class="fresh__line fresh__lag">
      <TriangleAlert aria-hidden="true" />
      <span>
        Кабинет «{{ a.name }}» {{ a.stats_at ? `не обновлялся с ${clock(a.stats_at)}` : 'ещё не отдал статистику' }} —
        <button class="link-btn" type="button" :disabled="isUpdating(a)" @click="refresh(a)">
          {{ isUpdating(a) ? 'обновляем…' : 'обновить' }}
        </button>
      </span>
    </p>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Clock, TriangleAlert } from '@lucide/vue'
import { errorText, webApi } from '../api/web'
import { refreshLoads, useLoads } from '../composables/useLoads'
import { pushToast } from '../composables/useToasts'
import { formatDateTime } from '../utils/format'

/**
 * Строка свежести под цифрами отчёта: «Данные на 14:20 (Директ) и 13:50 (Метрика)». Время Директа — по самому
 * старому кабинету проекта: пока один отстал, суммы за период неполные. Отставший кабинет — отдельной жёлтой
 * строкой со ссылкой «обновить» (обычная загрузка кабинета). Когда его загрузка закончится — emit('reload').
 */
const props = defineProps({
  /** freshness из отчёта: direct_at, metrika_at, loading, eta_minutes, accounts[{id, name, stats_at, lagging, updating}]. */
  freshness: { type: Object, default: null },
  /** Только отставшие кабинеты — когда время данных уже сказано рядом (пустой отчёт при первой загрузке). */
  onlyLagging: Boolean,
})
const emit = defineEmits(['reload'])

const started = ref(new Set())

const lagging = computed(() => (props.freshness?.accounts || []).filter((a) => a.lagging))

/** «14:20», если сегодня; иначе «18 нояб., 09:00». */
function clock(iso) {
  const d = new Date(iso)
  if (d.toDateString() === new Date().toDateString()) {
    return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  }
  return formatDateTime(iso)
}

const mainText = computed(() => {
  const f = props.freshness
  if (!f.direct_at && f.loading) {
    return f.eta_minutes ? `Загружаем статистику из Директа, осталось ~${f.eta_minutes} мин` : 'Загружаем статистику из Директа'
  }
  if (!f.direct_at) return 'Статистику из Директа ещё не загружали'
  const direct = `Данные на ${clock(f.direct_at)} (Директ)`
  return f.metrika_at ? `${direct} и ${clock(f.metrika_at)} (Метрика)` : direct
})

function isUpdating(a) {
  return a.updating || started.value.has(a.id)
}

async function refresh(a) {
  started.value = new Set([...started.value, a.id])
  try {
    await webApi.refreshAccount(a.id)
    pushToast(`Обновляем кабинет «${a.name}» — цифры обновятся сами, когда загрузка закончится`)
    refreshLoads()
  } catch (err) {
    const rest = new Set(started.value)
    rest.delete(a.id)
    started.value = rest
    pushToast(errorText(err), 'error')
  }
}

// Загрузка кабинета этого отчёта закончилась — перечитать цифры.
const loads = useLoads({
  onFinished(keys) {
    const mine = new Set((props.freshness?.accounts || []).map((a) => `a${a.id}`))
    if (keys.some((k) => mine.has(k))) {
      started.value = new Set()
      emit('reload')
    }
  },
})
onMounted(loads.start)
onBeforeUnmount(loads.stop)
</script>

<style scoped>
.fresh {
  display: grid;
  gap: 4px;
  margin: 6px 0 0;
}
.fresh__line {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 0;
  color: var(--faint);
  font-size: 12px;
  line-height: 1.45;
}
.fresh__line svg {
  flex: none;
  width: 14px;
  height: 14px;
  margin-top: 2px;
}
.fresh__lag {
  color: var(--warn);
}
.fresh__lag .link-btn {
  color: inherit;
  font-weight: 700;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.fresh__lag .link-btn:disabled {
  cursor: default;
  text-decoration: none;
}
</style>
