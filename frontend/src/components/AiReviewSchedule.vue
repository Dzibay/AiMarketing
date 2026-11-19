<template>
  <section class="panel">
    <h2>Разбор недели</h2>
    <p class="hint">
      Раз в неделю ИИ сам разбирает проекты — каждый в новом чате «Разбор недели». Что поправить, он предлагает
      карточками, решаете вы: в Директе разбор ничего не меняет. Итог — одна сводка в колокольчике и в Telegram.
    </p>
    <p v-if="!state" class="empty">{{ loadError || 'Загружаем…' }}</p>
    <template v-else>
      <UiSwitch class="check-row" :checked="state.enabled" :disabled="busy" @change="toggle($event.target.checked)">
        Разбирать каждую неделю
      </UiSwitch>
      <p v-if="!state.ai_ready" class="hint tone-warn">ИИ пока не подключён — разбор не запустится.</p>
      <div class="filters">
        <div v-field class="field">
          <label>День</label>
          <UiSelect
            :model-value="state.weekday"
            :options="DAYS"
            :searchable="false"
            :disabled="busy"
            @change="save({ weekday: $event })"
          />
        </div>
        <div v-field class="field">
          <label>Время, МСК</label>
          <UiSelect
            :model-value="state.hour"
            :options="HOURS"
            :searchable="false"
            :disabled="busy"
            @change="save({ hour: $event })"
          />
        </div>
      </div>
      <div class="seg" role="group" aria-label="Какие проекты разбирать">
        <button
          type="button"
          :class="{ active: state.scope === 'all' }"
          :aria-pressed="state.scope === 'all'"
          :disabled="busy"
          @click="setScope('all')"
        >
          Все проекты
        </button>
        <button
          type="button"
          :class="{ active: state.scope === 'selected' }"
          :aria-pressed="state.scope === 'selected'"
          :disabled="busy"
          @click="setScope('selected')"
        >
          Выбранные
        </button>
      </div>
      <ul v-if="state.scope === 'selected'" class="review-list">
        <li v-for="p in state.projects" :key="p.id">
          <UiCheckbox class="check-row" :checked="picked.has(p.id)" :disabled="busy" @change="pick(p.id, $event.target.checked)">
            {{ niceQuotes(p.name) }}<small v-if="p.client_name"> · {{ niceQuotes(p.client_name) }}</small>
          </UiCheckbox>
        </li>
        <li v-if="!state.projects.length" class="empty">Проектов пока нет.</li>
      </ul>
      <p class="sync-status">{{ costText }}</p>
      <p v-if="state.enabled && state.next_run_at" class="sync-status">
        Следующий разбор — {{ formatDateTime(state.next_run_at) }}
      </p>
      <p class="hint">
        Если лимита ИИ не хватит на все проекты, часть пропустим — об этом будет в сводке. Одна неделя — один разбор.
      </p>
      <p v-if="error" class="form-error">{{ error }}</p>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { aiScheduleApi, errorText } from '../api/web'
import { formatDateTime, limitPct, niceQuotes, plural } from '../utils/format'
import UiCheckbox from './ui/UiCheckbox.vue'
import UiSelect from './ui/UiSelect.vue'
import UiSwitch from './ui/UiSwitch.vue'

/**
 * «Разбор недели» в Настройках → ИИ: включается только самим пользователем, по умолчанию выключен.
 * День, час (МСК), все проекты или выбранные и примерная доля лимита в неделю — без денег и единиц.
 */
const DAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'].map((label, value) => ({
  value,
  label,
}))
const HOURS = Array.from({ length: 24 }, (_, h) => ({ value: h, label: `${String(h).padStart(2, '0')}:00` }))

const state = ref(null)
const loadError = ref('')
const error = ref('')
const busy = ref(false)

const picked = computed(() => new Set(state.value?.workspace_ids || []))
const count = computed(() => {
  const s = state.value
  if (!s) return 0
  if (s.scope === 'all') return s.projects.length
  const own = new Set(s.projects.map((p) => p.id))
  return s.workspace_ids.filter((id) => own.has(id)).length
})

const costText = computed(() => {
  const s = state.value
  const n = count.value
  const what = `${n} ${plural(n, 'проект', 'проекта', 'проектов')}`
  if (s.unlimited) return `Разбор: ${what}. Лимит ИИ у вас не ограничен.`
  if (s.per_project_percent === null || s.per_project_percent === undefined) {
    return `Разбор: ${what}. Сколько уйдёт лимита, покажем после первого разбора.`
  }
  return `Разбор: ${what} — примерно ${limitPct(s.per_project_percent * n)} лимита в неделю.`
})

async function load() {
  try {
    state.value = await aiScheduleApi.get()
  } catch (err) {
    loadError.value = errorText(err)
  }
}

async function save(patch) {
  busy.value = true
  error.value = ''
  try {
    const s = state.value
    state.value = await aiScheduleApi.set({
      enabled: s.enabled,
      weekday: s.weekday,
      hour: s.hour,
      scope: s.scope,
      workspace_ids: s.workspace_ids,
      ...patch,
    })
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

function toggle(enabled) {
  save({ enabled })
}

function setScope(scope) {
  if (state.value.scope === scope) return
  // Выбранные без единой галочки не сохранить включёнными — начинаем со всех проектов.
  const ids = scope === 'selected' && !state.value.workspace_ids.length ? state.value.projects.map((p) => p.id) : undefined
  save(ids ? { scope, workspace_ids: ids } : { scope })
}

function pick(id, on) {
  const ids = new Set(state.value.workspace_ids)
  if (on) ids.add(id)
  else ids.delete(id)
  save({ workspace_ids: [...ids] })
}

onMounted(load)
</script>

<style scoped>
.review-list {
  display: grid;
  gap: 8px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}
.seg {
  margin-top: 12px;
}
</style>
