<template>
  <Modal :open="open" title="Новая кампания" wide :locked="saving" @close="$emit('close')">
    <p v-if="loadError" class="form-error">{{ loadError }}</p>
    <p v-else-if="!options" class="empty">Загружаем справочники…</p>

    <template v-else>
      <ol class="wizard-steps">
        <li v-for="(s, i) in STEPS" :key="s" :class="{ active: i === step, done: i < step }">
          <button type="button" :disabled="i > maxStep" @click="step = i">{{ i + 1 }}. {{ s }}</button>
        </li>
      </ol>

      <form id="campaign-wizard" @submit.prevent="next">
        <!-- 1. Кабинет -->
        <div v-show="step === 0" class="form-grid">
          <div v-field class="field field--wide">
            <label>Кабинет Директа, где создать кампанию</label>
            <select v-model.number="form.account_id" required>
              <option :value="null" disabled>Выберите кабинет</option>
              <optgroup v-if="accounts.length" label="Кабинеты пространства">
                <option v-for="a in accounts" :key="a.id" :value="a.id" :disabled="!a.enabled">
                  {{ accountLabel(a) }}{{ a.enabled ? '' : ' — выключен' }}
                </option>
              </optgroup>
              <optgroup v-if="otherAccounts.length" label="Другие кабинеты — подключатся к пространству">
                <option v-for="a in otherAccounts" :key="a.id" :value="a.id">{{ accountLabel(a) }}</option>
              </optgroup>
            </select>
            <p v-if="!accounts.length && !otherAccounts.length" class="field-hint tone-warn">
              Нет ни одного кабинета Директа — подключите логин на странице
              <RouterLink to="/cabinet/accounts">«Аккаунты»</RouterLink>.
            </p>
            <p v-else-if="account" class="field-hint" :class="COUNTER_TONES[account.counter_access?.status]">
              Счётчик № {{ options.counter_id }}:
              {{ account.counter_access?.status === 'ok' ? 'у логина кабинета есть доступ, цели Метрики доступны в стратегиях.' : account.counter_access?.hint || 'проверка не выполнялась' }}
              <template v-if="otherAccounts.some((a) => a.id === account.id)">
                Кабинет будет подключён к пространству вместе с новой кампанией.
              </template>
            </p>
          </div>
          <div v-field class="field">
            <label>Тип</label>
            <select v-model="form.type">
              <option v-for="t in options.types" :key="t.id" :value="t.id">{{ t.label }}</option>
            </select>
          </div>
          <div v-field class="field">
            <label>Название</label>
            <input v-model.trim="form.name" type="text" maxlength="255" required />
          </div>
          <div v-field class="field">
            <label>Начало показов</label>
            <input v-model="form.start_date" type="date" :min="today" />
          </div>
          <div v-field class="field">
            <label>Окончание (необязательно)</label>
            <input v-model="form.end_date" type="date" :min="form.start_date || today" />
          </div>
          <p class="field-hint field--wide">
            Счётчик пространства № {{ options.counter_id }} подключится к кампании автоматически — без него Директ не
            увидит цели и конверсии. Кампания создаётся черновиком и не тратит деньги, пока вы не отправите объявления на
            модерацию.
          </p>
        </div>

        <!-- 2. Стратегия -->
        <div v-show="step === 1">
          <StrategyFields
            v-model="form.strategy"
            :options="options"
            :campaign-type="form.type"
            :goals="options.goals"
            :default-goal-id="options.goal_id"
          />
          <div class="form-grid">
            <div v-if="dailyBudget" v-field class="field">
              <label>Дневной бюджет, ₽ (необязательно)</label>
              <input
                v-model="form.daily_budget_rub"
                type="number"
                :min="options.min_daily_budget_rub"
                step="1"
                :placeholder="`от ${options.min_daily_budget_rub}`"
              />
            </div>
            <div v-field class="field">
              <label>Модель атрибуции</label>
              <select v-model="form.attribution_model">
                <option value="">По умолчанию Директа</option>
                <option v-for="m in options.attribution_models" :key="m.id" :value="m.id">{{ m.label }}</option>
              </select>
            </div>
            <div v-field class="field field--wide">
              <label>Ключевые цели (для оптимизации и отчётов)</label>
              <div v-for="(g, i) in form.priority_goals" :key="i" class="filters filters--inline">
                <select v-model.number="g.goal_id">
                  <option :value="null">Цель</option>
                  <option v-for="goal in options.goals" :key="goal.goal_id" :value="goal.goal_id">{{ goal.name }}</option>
                </select>
                <input v-model.number="g.value_rub" type="number" min="0.3" step="any" placeholder="ценность от 0,3 ₽" />
                <button class="btn btn--ghost" type="button" @click="form.priority_goals.splice(i, 1)">Убрать</button>
              </div>
              <button
                class="btn btn--ghost"
                type="button"
                :disabled="!options.goals.length"
                @click="form.priority_goals.push({ goal_id: options.goal_id || null, value_rub: null })"
              >
                Добавить цель
              </button>
              <p v-if="!options.goals.length" class="field-hint">У счётчика нет загруженных целей.</p>
            </div>
          </div>
        </div>

        <!-- 3. Показы -->
        <div v-show="step === 2" class="form-grid">
          <div v-field class="field field--wide">
            <label>Расписание показов</label>
            <ScheduleGrid v-model="form.schedule" />
            <label class="check-row">
              <input v-model="form.consider_working_weekends" type="checkbox" />
              Учитывать перенос рабочих дней в праздники
            </label>
          </div>
          <div v-field class="field field--wide">
            <label>Минус-фразы кампании — по одной на строку</label>
            <textarea v-model="form.negative_keywords" rows="4" placeholder="бесплатно&#10;своими руками&#10;вакансии" />
          </div>
          <div v-field class="field field--wide">
            <label>Запрещённые площадки РСЯ — домены через запятую или с новой строки</label>
            <textarea v-model="form.excluded_sites" rows="2" placeholder="games.example.ru" />
          </div>
          <div v-field class="field field--wide">
            <label>Параметры ссылок (UTM)</label>
            <input
              v-model.trim="form.tracking_params"
              type="text"
              placeholder="utm_source=yandex&utm_medium=cpc&utm_campaign={campaign_id}"
            />
          </div>
          <div v-field class="field field--wide">
            <label>Опции</label>
            <label v-for="o in options.settings" :key="o.id" class="check-row">
              <input v-model="form.settings[o.id]" type="checkbox" />
              {{ o.label }}
            </label>
          </div>
        </div>

        <!-- 4. Первая группа -->
        <div v-show="step === 3" class="form-grid">
          <label class="check-row field--wide">
            <input v-model="form.with_group" type="checkbox" />
            Сразу создать первую группу с фразами и объявлением
          </label>
          <template v-if="form.with_group">
            <div v-field class="field">
              <label>Название группы</label>
              <input v-model.trim="form.group.name" type="text" maxlength="255" />
            </div>
            <div v-field class="field">
              <label>Регион показа</label>
              <select v-model="form.group.region">
                <option v-for="r in REGIONS" :key="r.id" :value="r.id">{{ r.label }}</option>
                <option value="custom">Свой список ID регионов</option>
              </select>
            </div>
            <div v-if="form.group.region === 'custom'" v-field class="field field--wide">
              <label>ID регионов Директа через запятую (минус — исключить)</label>
              <input v-model.trim="form.group.region_ids" type="text" placeholder="213, 2, -10174" />
            </div>
            <div v-field class="field field--wide">
              <label>Ключевые фразы — по одной на строку</label>
              <textarea v-model="form.group.keywords" rows="5" placeholder="купить диван&#10;диван недорого -бу" />
            </div>
            <div v-if="manual" v-field class="field">
              <label>Ставка, ₽</label>
              <input v-model="form.group.bid_rub" type="number" min="0.3" step="0.1" placeholder="например, 15" />
            </div>
            <div v-field class="field field--wide">
              <label>Заголовок 1 <span class="field-hint">{{ form.group.ad.title.length }}/56</span></label>
              <input v-model="form.group.ad.title" type="text" maxlength="56" />
            </div>
            <div v-if="!responsiveAd" v-field class="field">
              <label>Заголовок 2 <span class="field-hint">{{ form.group.ad.title2.length }}/30</span></label>
              <input v-model="form.group.ad.title2" type="text" maxlength="30" />
            </div>
            <div v-field class="field">
              <label>Ссылка</label>
              <input v-model.trim="form.group.ad.href" type="url" placeholder="https://…" />
            </div>
            <div v-field class="field field--wide">
              <label>Текст <span class="field-hint">{{ form.group.ad.text.length }}/81</span></label>
              <textarea v-model="form.group.ad.text" rows="2" maxlength="81" />
            </div>
            <template v-if="responsiveAd">
              <div v-field class="field field--wide">
                <label>Ещё заголовки — по одному на строку, до 6 <span class="field-hint">до 56 символов</span></label>
                <textarea v-model="form.group.ad.more_titles" rows="3" />
              </div>
              <div v-field class="field field--wide">
                <label>Ещё тексты — по одному на строку, до 2 <span class="field-hint">до 81 символа</span></label>
                <textarea v-model="form.group.ad.more_texts" rows="2" />
              </div>
              <p class="field-hint field--wide">
                Объявление комбинаторное: Директ сам собирает показы из заголовков и текстов.
              </p>
            </template>
            <AdExtrasFields
              :extras="form.group.ad.extras"
              :account-id="form.account_id"
              :max-images="responsiveAd ? 5 : 1"
              :disabled="saving"
            />
          </template>
          <p v-else class="field-hint field--wide">
            Группы, фразы и объявления можно добавить потом на странице кампании.
          </p>
        </div>

        <!-- 5. Проверка -->
        <div v-show="step === 4">
          <dl class="summary-list">
            <dt>Кабинет</dt>
            <dd>{{ account ? account.client_login : '—' }}{{ account?.sandbox ? ' (песочница)' : '' }}</dd>
            <dt>Кампания</dt>
            <dd>{{ form.name }} · {{ typeLabel }}</dd>
            <dt>Период</dt>
            <dd>с {{ form.start_date ? formatDate(form.start_date) : 'сегодня' }}{{ form.end_date ? ` по ${formatDate(form.end_date)}` : ', без окончания' }}</dd>
            <dt>Стратегия</dt>
            <dd>{{ strategySummary }}</dd>
            <dt>Расписание</dt>
            <dd>{{ form.schedule ? 'по расписанию' : 'круглосуточно' }}</dd>
            <dt>Минус-фраз</dt>
            <dd>{{ lines(form.negative_keywords).length }}</dd>
            <dt>Первая группа</dt>
            <dd>
              <template v-if="form.with_group">
                «{{ form.group.name || 'Группа 1' }}», фраз: {{ lines(form.group.keywords).length }},
                {{ form.group.ad.title && form.group.ad.text ? 'с объявлением' : 'без объявления' }}
              </template>
              <template v-else>нет</template>
            </dd>
          </dl>
          <p class="field-hint">Кампания появится в Директе черновиком и сразу добавится в это пространство.</p>
        </div>
        <p v-if="formError" class="form-error">{{ formError }}</p>
      </form>
    </template>

    <template #footer>
      <button v-if="step > 0" class="btn btn--ghost" type="button" :disabled="saving" @click="step -= 1">Назад</button>
      <button class="btn btn--ghost" type="button" :disabled="saving" @click="$emit('close')">Отмена</button>
      <button class="btn btn--accent" type="submit" form="campaign-wizard" :disabled="saving || !options">
        {{ step < STEPS.length - 1 ? 'Далее' : saving ? 'Создаём…' : 'Создать кампанию' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import Modal from '../../components/ui/Modal.vue'
import ScheduleGrid from '../../components/ScheduleGrid.vue'
import StrategyFields from '../../components/StrategyFields.vue'
import AdExtrasFields from '../../components/AdExtrasFields.vue'
import { errorText, wsApi } from '../../api/web'
import { formatDate, isoDay, priorityGoalsForApi } from '../../utils/format'
import { emptyExtras, extrasEmpty, extrasPayload, extrasProblem } from '../../utils/adExtras'

const props = defineProps({
  open: { type: Boolean, default: false },
  workspace: { type: Object, required: true },
})
const emit = defineEmits(['close', 'created'])

const STEPS = ['Кабинет', 'Стратегия', 'Показы', 'Первая группа', 'Проверка']
const REGIONS = [
  { id: '225', label: 'Россия', ids: [225] },
  { id: '1', label: 'Москва и область', ids: [1] },
  { id: '213', label: 'Москва', ids: [213] },
  { id: '10174', label: 'Санкт-Петербург и область', ids: [10174] },
  { id: '2', label: 'Санкт-Петербург', ids: [2] },
  { id: '225-1', label: 'Россия без Москвы и области', ids: [225, -1] },
]
const COUNTER_TONES = { ok: 'tone-ok', unknown: 'tone-warn', sandbox: '' }

const options = ref(null)
const loadError = ref('')
const step = ref(0)
const maxStep = ref(0)
const saving = ref(false)
const formError = ref('')
const today = isoDay(new Date())

function blankForm() {
  const site = (props.workspace.site || '').trim()
  return {
    account_id: null,
    type: 'TEXT_CAMPAIGN',
    name: `${props.workspace.name} — поиск`,
    start_date: today,
    end_date: '',
    strategy: { search: 'HIGHEST_POSITION', network: 'SERVING_OFF' },
    daily_budget_rub: '',
    attribution_model: '',
    priority_goals: [],
    schedule: null,
    consider_working_weekends: true,
    negative_keywords: '',
    excluded_sites: '',
    tracking_params: '',
    settings: {},
    with_group: true,
    group: {
      name: 'Группа 1',
      region: '225',
      region_ids: '',
      keywords: '',
      bid_rub: '',
      ad: {
        title: '',
        title2: '',
        text: '',
        more_titles: '',
        more_texts: '',
        href: site ? (/^https?:\/\//i.test(site) ? site : `https://${site}`) : '',
        extras: emptyExtras(),
      },
    },
  }
}

const form = reactive(blankForm())
const accounts = computed(() => options.value?.accounts || [])
const otherAccounts = computed(() => options.value?.other_accounts || [])
const account = computed(
  () => [...accounts.value, ...otherAccounts.value].find((a) => a.id === form.account_id) || null,
)

function accountLabel(a) {
  return `${a.client_login}${a.name && a.name !== a.client_login ? ` — ${a.name}` : ''}${a.sandbox ? ' (песочница)' : ''}`
}
const manual = computed(
  () =>
    form.strategy.search === 'HIGHEST_POSITION' ||
    (form.strategy.search === 'SERVING_OFF' && form.strategy.network === 'MAXIMUM_COVERAGE'),
)
const unified = computed(() => form.type === 'UNIFIED_CAMPAIGN')
// Новые объявления текстовых кампаний и ЕПК Директ делает комбинаторными: до 7 заголовков, без второго.
const responsiveAd = computed(() => ['TEXT_CAMPAIGN', 'UNIFIED_CAMPAIGN'].includes(form.type))
// Недельный бюджет ручных ставок ЕПК заменяет дневной — оба Директ не принимает.
const dailyBudget = computed(() => manual.value && !(unified.value && form.strategy.weekly_budget_rub))
const typeLabel = computed(() => options.value?.types.find((t) => t.id === form.type)?.label || form.type)
const strategySummary = computed(() => {
  const o = options.value
  if (!o) return ''
  const t = o.by_type?.[form.type] || o
  const s = t.search_strategies.find((x) => x.id === form.strategy.search)?.label || form.strategy.search
  const net = Object.values(t.network_by_search || {})
    .flat()
    .find((x) => x.id === form.strategy.network)?.label
  const parts = [`поиск: ${s}`, `сети: ${net || form.strategy.network || '—'}`]
  if (form.strategy.weekly_budget_rub) parts.push(`неделя ${form.strategy.weekly_budget_rub} ₽`)
  if (form.strategy.average_cpc_rub) parts.push(`клик ${form.strategy.average_cpc_rub} ₽`)
  if (form.strategy.average_cpa_rub) parts.push(`конверсия ${form.strategy.average_cpa_rub} ₽`)
  if (dailyBudget.value && form.daily_budget_rub) parts.push(`день ${form.daily_budget_rub} ₽`)
  return parts.join(' · ')
})

function lines(text) {
  return String(text || '')
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
}

async function loadOptions() {
  loadError.value = ''
  try {
    const data = await wsApi.campaignOptions(props.workspace.id)
    options.value = data
    for (const o of data.settings) if (!(o.id in form.settings)) form.settings[o.id] = !!o.default
    if (!form.account_id) {
      const usable = data.accounts.filter((a) => a.enabled)
      form.account_id = (usable.find((a) => a.counter_access?.status === 'ok') || usable[0])?.id ?? null
    }
  } catch (err) {
    loadError.value = errorText(err)
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) return
    Object.assign(form, blankForm())
    step.value = 0
    maxStep.value = 0
    formError.value = ''
    loadOptions()
  },
  { immediate: true },
)

function validate() {
  if (step.value === 0) {
    if (!form.account_id) return 'Выберите кабинет Директа'
    if (!form.name) return 'Укажите название'
  }
  if (step.value === 1) {
    const daily = Number(form.daily_budget_rub)
    if (dailyBudget.value && form.daily_budget_rub !== '' && daily < options.value.min_daily_budget_rub)
      return `Дневной бюджет — от ${options.value.min_daily_budget_rub} ₽`
    const weekly = Number(form.strategy.weekly_budget_rub)
    if (form.strategy.weekly_budget_rub != null && weekly && weekly < options.value.min_weekly_budget_rub)
      return `Недельный бюджет — от ${options.value.min_weekly_budget_rub} ₽`
  }
  if (step.value === 3 && form.with_group) {
    const ad = form.group.ad
    const partial = [ad.title, ad.text, ad.href].filter(Boolean).length
    if (partial && partial < 3) return 'Для объявления нужны заголовок, текст и ссылка — или оставьте все поля пустыми'
    if (partial && responsiveAd.value) {
      const titles = lines(ad.more_titles)
      const texts = lines(ad.more_texts)
      if (titles.length > 6) return `Заголовков — не больше 7 вместе с первым, сейчас ${titles.length + 1}`
      if (texts.length > 2) return `Текстов — не больше 3 вместе с первым, сейчас ${texts.length + 1}`
      const long = titles.find((t) => t.length > 56) || texts.find((t) => t.length > 81)
      if (long) return `«${long}» — ${long.length} символов, длиннее предела Директа: сократите`
    }
    const extrasError = extrasProblem(ad.extras, responsiveAd.value ? 5 : 1)
    if (extrasError) return extrasError
    if (form.group.region === 'custom' && !regionIds().length) return 'Укажите ID регионов'
  }
  return ''
}

function regionIds() {
  if (form.group.region !== 'custom') return REGIONS.find((r) => r.id === form.group.region)?.ids || [225]
  return form.group.region_ids
    .split(/[\s,;]+/)
    .map((v) => Number(v))
    .filter((v) => Number.isInteger(v) && v !== 0)
}

function buildSpec() {
  const spec = {
    type: form.type,
    name: form.name,
    start_date: form.start_date || undefined,
    end_date: form.end_date || undefined,
    strategy: { ...form.strategy },
    negative_keywords: form.negative_keywords,
    excluded_sites: form.excluded_sites,
    schedule: form.schedule || 'always',
    consider_working_weekends: form.consider_working_weekends,
    settings: { ...form.settings },
    attribution_model: form.attribution_model || undefined,
    tracking_params: form.tracking_params || undefined,
    priority_goals: priorityGoalsForApi(form.priority_goals),
  }
  if (dailyBudget.value && form.daily_budget_rub !== '') spec.daily_budget_rub = Number(form.daily_budget_rub)
  if (form.with_group) {
    const g = form.group
    spec.group = { name: g.name || 'Группа 1', region_ids: regionIds(), keywords: g.keywords }
    if (manual.value && g.bid_rub !== '') spec.group.bid_rub = Number(g.bid_rub)
    if (g.ad.title && g.ad.text && g.ad.href) {
      const { more_titles: moreTitles, more_texts: moreTexts, extras, ...ad } = g.ad
      if (!extrasEmpty(extras)) ad.extras = extrasPayload(extras)
      if (responsiveAd.value) {
        ad.title2 = ''
        ad.titles = [ad.title, ...lines(moreTitles)]
        ad.texts = [ad.text, ...lines(moreTexts)]
      }
      spec.group.ad = ad
    }
  }
  return spec
}

async function next() {
  formError.value = validate()
  if (formError.value) return
  if (step.value < STEPS.length - 1) {
    step.value += 1
    maxStep.value = Math.max(maxStep.value, step.value)
    return
  }
  saving.value = true
  try {
    const result = await wsApi.createCampaign(props.workspace.id, form.account_id, buildSpec())
    emit('created', result)
  } catch (err) {
    formError.value = errorText(err)
  } finally {
    saving.value = false
  }
}
</script>
