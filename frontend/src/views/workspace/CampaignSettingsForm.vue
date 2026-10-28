<template>
  <div v-if="loading" class="panel"><p class="empty">Читаем настройки кампании из Директа…</p></div>

  <!-- Типы, которые мастер настроек не поддерживает: оставляем минус-фразы, бюджет и название. -->
  <section v-else-if="unsupported" class="panel">
    <h2>Настройки</h2>
    <p class="hint">
      Полный редактор работает для текстово-графических кампаний и ЕПК. Для этого типа доступны название, дневной бюджет
      и минус-фразы — остальное меняется в интерфейсе Директа.
    </p>
    <form class="form-grid" @submit.prevent="saveBasic">
      <div class="field field--wide">
        <label>Название</label>
        <input v-model.trim="basic.name" type="text" maxlength="255" />
      </div>
      <div class="field">
        <label>Дневной бюджет, ₽ (0 — без ограничения)</label>
        <input v-model="basic.daily_budget_rub" type="number" min="0" step="1" />
      </div>
      <div class="field field--wide">
        <label>Минус-фразы — по одной на строку</label>
        <textarea v-model="basic.negative_text" rows="6" />
      </div>
      <div class="form-actions">
        <button class="btn btn--accent" type="submit" :disabled="saving">Сохранить</button>
      </div>
    </form>
  </section>

  <div v-else-if="!original" class="panel">
    <p class="empty">{{ loadError || 'Настройки недоступны.' }}</p>
    <button class="btn btn--ghost" type="button" @click="load">Повторить</button>
  </div>

  <form v-else class="campaign-settings" @submit.prevent="save">
    <section class="panel">
      <h2>Основное</h2>
      <div class="form-grid">
        <div class="field field--wide">
          <label>Название</label>
          <input v-model.trim="form.name" type="text" maxlength="255" required />
        </div>
        <div class="field">
          <label>Начало</label>
          <input v-model="form.start_date" type="date" />
        </div>
        <div class="field">
          <label>Окончание (пусто — без даты)</label>
          <input v-model="form.end_date" type="date" :min="form.start_date" />
        </div>
        <!-- «Информации о клиенте» нет: Директ ведёт кампании как ЕПК, где ClientInfo не поддерживается. -->
      </div>
    </section>

    <section class="panel">
      <h2>Стратегия и бюджет</h2>
      <p class="hint">Сейчас: {{ original.strategy_label || '—' }}</p>
      <StrategyFields
        v-if="options"
        v-model="form.strategy"
        :options="options"
        :campaign-type="original?.type || ''"
        :goals="options.goals || []"
        :default-goal-id="workspace.goal_id"
      />
      <div class="form-grid">
        <div v-if="dailyBudget" class="field">
          <label>Дневной бюджет, ₽ (0 — без ограничения)</label>
          <input v-model="form.daily_budget_rub" type="number" min="0" step="1" />
        </div>
        <div class="field">
          <label>Модель атрибуции</label>
          <select v-model="form.attribution_model">
            <option value="" disabled>Не задана</option>
            <option v-for="m in options?.attribution_models || []" :key="m.id" :value="m.id">{{ m.label }}</option>
          </select>
        </div>
        <div class="field field--wide">
          <label>Ключевые цели</label>
          <div v-for="(g, i) in form.priority_goals" :key="i" class="filters filters--inline">
            <select v-model.number="g.goal_id">
              <option :value="null">Цель</option>
              <option v-for="goal in options?.goals || []" :key="goal.goal_id" :value="goal.goal_id">
                {{ goal.name }}
              </option>
              <option v-if="g.goal_id && !goalKnown(g.goal_id)" :value="g.goal_id">Цель {{ g.goal_id }}</option>
            </select>
            <input v-model.number="g.value_rub" type="number" min="0.3" step="any" placeholder="ценность от 0,3 ₽" />
            <button class="btn btn--ghost" type="button" @click="form.priority_goals.splice(i, 1)">Убрать</button>
          </div>
          <button class="btn btn--ghost" type="button" @click="form.priority_goals.push({ goal_id: null, value_rub: null })">
            Добавить цель
          </button>
        </div>
        <div class="field field--wide">
          <label>Счётчики Метрики — номера через запятую</label>
          <input v-model.trim="form.counter_ids" type="text" />
          <p v-if="!counterIds().includes(workspace.counter_id)" class="field-hint tone-warn">
            Счётчик пространства № {{ workspace.counter_id }} не подключён — конверсии по его целям Директ не увидит.
          </p>
        </div>
      </div>
    </section>

    <section class="panel">
      <h2>Показы</h2>
      <div class="form-grid">
        <div class="field field--wide">
          <label>Расписание</label>
          <ScheduleGrid v-model="form.schedule" />
          <label class="check-row">
            <input v-model="form.consider_working_weekends" type="checkbox" />
            Учитывать перенос рабочих дней в праздники
          </label>
        </div>
        <div class="field field--wide">
          <label>Минус-фразы кампании — по одной на строку ({{ lines(form.negative_keywords).length }})</label>
          <textarea v-model="form.negative_keywords" rows="8" />
        </div>
        <div class="field field--wide">
          <label>Запрещённые площадки — по одной на строку</label>
          <textarea v-model="form.excluded_sites" rows="3" />
        </div>
        <div class="field field--wide">
          <label>Параметры ссылок (UTM)</label>
          <input v-model.trim="form.tracking_params" type="text" placeholder="utm_source=yandex&utm_medium=cpc" />
        </div>
        <div class="field field--wide">
          <label>Опции</label>
          <label v-for="o in options?.settings || []" :key="o.id" class="check-row">
            <input v-model="form.settings[o.id]" type="checkbox" />
            {{ o.label }}
          </label>
        </div>
      </div>
    </section>

    <div class="form-actions form-actions--sticky">
      <span class="hint">{{ changedLabels.length ? `Изменено: ${changedLabels.join(', ')}` : 'Изменений нет' }}</span>
      <button class="btn btn--ghost" type="button" :disabled="saving || !changedLabels.length" @click="reset">
        Отменить правки
      </button>
      <button class="btn btn--accent" type="submit" :disabled="saving || !changedLabels.length">
        {{ saving ? 'Сохраняем…' : 'Сохранить в Директ' }}
      </button>
    </div>
  </form>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import ScheduleGrid from '../../components/ScheduleGrid.vue'
import StrategyFields from '../../components/StrategyFields.vue'
import { errorText, webApi, wsApi } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import { priorityGoalsForApi } from '../../utils/format'

const props = defineProps({
  workspace: { type: Object, required: true },
  campaignId: { type: Number, required: true },
  // Строка кампании из локальной базы (для упрощённого режима).
  campaign: { type: Object, default: null },
})
const emit = defineEmits(['saved'])

const LABELS = {
  name: 'название',
  start_date: 'начало',
  end_date: 'окончание',
  client_info: 'информация о клиенте',
  strategy: 'стратегия',
  daily_budget_rub: 'дневной бюджет',
  attribution_model: 'атрибуция',
  priority_goals: 'ключевые цели',
  counter_ids: 'счётчики',
  schedule: 'расписание',
  negative_keywords: 'минус-фразы',
  excluded_sites: 'площадки',
  tracking_params: 'UTM',
  settings: 'опции',
}

const loading = ref(true)
const loadError = ref('')
const unsupported = ref(false)
const saving = ref(false)
const original = ref(null)
const options = ref(null)
const form = reactive({})
const basic = reactive({ name: '', daily_budget_rub: '', negative_text: '' })

const manual = computed(
  () =>
    form.strategy?.search === 'HIGHEST_POSITION' ||
    (form.strategy?.search === 'SERVING_OFF' && form.strategy?.network === 'MAXIMUM_COVERAGE'),
)
// Недельный бюджет ручных ставок ЕПК заменяет дневной — оба Директ не принимает.
const dailyBudget = computed(
  () => manual.value && !(original.value?.type === 'UNIFIED_CAMPAIGN' && form.strategy?.weekly_budget_rub),
)

function lines(text) {
  return String(text || '')
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
}

function counterIds() {
  return String(form.counter_ids || '')
    .split(/[\s,;]+/)
    .map(Number)
    .filter((n) => Number.isInteger(n) && n > 0)
}

function goalKnown(id) {
  return (options.value?.goals || []).some((g) => g.goal_id === id)
}

function cleanStrategy(s) {
  const out = {}
  for (const [k, v] of Object.entries(s || {})) if (k !== 'manual' && v != null && v !== '') out[k] = v
  return out
}

function toForm(o) {
  return {
    name: o.name || '',
    start_date: o.start_date || '',
    end_date: o.end_date || '',
    client_info: o.client_info || '',
    strategy: cleanStrategy(o.strategy),
    daily_budget_rub: o.daily_budget_rub ?? '',
    attribution_model: o.attribution_model || '',
    priority_goals: (o.priority_goals || []).map((g) => ({ ...g })),
    counter_ids: (o.counter_ids || []).join(', '),
    schedule: o.schedule ? o.schedule.map((r) => [...r]) : null,
    consider_working_weekends: o.consider_working_weekends !== false,
    negative_keywords: (o.negative_keywords || []).join('\n'),
    excluded_sites: (o.excluded_sites || []).join('\n'),
    tracking_params: o.tracking_params || '',
    settings: { ...(o.settings || {}) },
  }
}

const baseline = computed(() => (original.value ? toForm(original.value) : null))
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)

const changes = computed(() => {
  const b = baseline.value
  if (!b) return {}
  const out = {}
  for (const key of ['name', 'start_date', 'end_date', 'client_info', 'tracking_params']) {
    if (form[key] !== b[key]) out[key] = form[key]
  }
  if (!same(cleanStrategy(form.strategy), b.strategy)) out.strategy = cleanStrategy(form.strategy)
  if (dailyBudget.value && String(form.daily_budget_rub ?? '') !== String(b.daily_budget_rub ?? '')) {
    out.daily_budget_rub = form.daily_budget_rub === '' ? 0 : Number(form.daily_budget_rub)
  }
  if (form.attribution_model && form.attribution_model !== b.attribution_model) out.attribution_model = form.attribution_model
  // Цели уходят в Директ целиком (SET): цель, выпавшая из списка, удалилась бы из кампании.
  const goals = priorityGoalsForApi(form.priority_goals)
  if (!same(goals, priorityGoalsForApi(b.priority_goals))) out.priority_goals = goals
  if (!same(counterIds(), original.value.counter_ids || [])) out.counter_ids = counterIds()
  if (!same(form.schedule, b.schedule) || form.consider_working_weekends !== b.consider_working_weekends) {
    out.schedule = form.schedule || 'always'
    out.consider_working_weekends = form.consider_working_weekends
  }
  if (!same(lines(form.negative_keywords), lines(b.negative_keywords))) out.negative_keywords = lines(form.negative_keywords)
  if (!same(lines(form.excluded_sites), lines(b.excluded_sites))) out.excluded_sites = lines(form.excluded_sites)
  const settings = {}
  for (const [k, v] of Object.entries(form.settings)) if (!!v !== !!b.settings[k]) settings[k] = !!v
  if (Object.keys(settings).length) out.settings = settings
  return out
})

const changedLabels = computed(() =>
  Object.keys(changes.value)
    .filter((k) => k !== 'consider_working_weekends')
    .map((k) => LABELS[k] || k),
)

function reset() {
  if (baseline.value) Object.assign(form, toForm(original.value))
}

async function load() {
  loading.value = true
  loadError.value = ''
  unsupported.value = false
  try {
    const [settings, opts] = await Promise.all([
      wsApi.campaignSettings(props.workspace.id, props.campaignId),
      options.value ? Promise.resolve(options.value) : wsApi.campaignOptions(props.workspace.id),
    ])
    options.value = opts
    original.value = settings
    Object.assign(form, toForm(settings))
    for (const o of opts.settings) if (!(o.id in form.settings)) form.settings[o.id] = false
  } catch (err) {
    if (err?.code === 'unsupported') {
      unsupported.value = true
      const camp = props.campaign || (await wsApi.campaign(props.workspace.id, props.campaignId).catch(() => null))?.campaign
      basic.name = camp?.name || ''
      basic.daily_budget_rub = camp?.daily_budget_kopecks ? camp.daily_budget_kopecks / 100 : ''
      basic.negative_text = (camp?.negative_keywords || []).join('\n')
    } else {
      loadError.value = errorText(err)
    }
  } finally {
    loading.value = false
  }
}

async function save() {
  const payload = changes.value
  if (!Object.keys(payload).length) return
  saving.value = true
  try {
    const res = await wsApi.updateCampaignSettings(props.workspace.id, props.campaignId, payload)
    const warnings = res.warnings || []
    pushToast(
      `Сохранено: ${(res.changed || []).join(', ')}${warnings.length ? `. Директ предупреждает: ${warnings.join('; ')}` : ''}`,
      warnings.length ? 'warn' : 'ok',
    )
    emit('saved')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    saving.value = false
  }
}

async function saveBasic() {
  saving.value = true
  try {
    const payload = { negative_text: basic.negative_text }
    if (basic.name && basic.name !== props.campaign?.name) payload.name = basic.name
    if (basic.daily_budget_rub !== '') payload.daily_budget_rub = Number(basic.daily_budget_rub)
    await webApi.updateCampaign(props.campaignId, payload)
    pushToast('Сохранено в Директ', 'ok')
    emit('saved')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
