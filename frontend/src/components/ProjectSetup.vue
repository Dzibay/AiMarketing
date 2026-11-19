<template>
  <Modal :open="open" title="Подготовим проект" wide :locked="busy || draftBusy" @close="close">
    <p v-if="loading && !ws" class="field-hint">Открываем проект…</p>
    <template v-else-if="!ws">
      <p class="form-error">{{ loadError || 'Проект недоступен.' }}</p>
      <button class="btn btn--ghost" type="button" @click="load">Повторить</button>
    </template>
    <template v-else>
      <p class="ps-lead">
        Пока загружаются кампании и статистика, ответьте на пять коротких вопросов. Любой можно пропустить и
        вернуться к нему позже — на сводке проекта или в настройках.
        <b class="ps-count">Готово {{ doneCount }} из {{ steps.length }}</b>
      </p>

      <ol class="ps-steps">
        <li class="ps-step" :class="{ 'is-done': steps[0].done }">
          <h3 class="ps-step__h"><component :is="steps[0].done ? CircleCheck : Circle" aria-hidden="true" />Клиент</h3>
          <div v-field class="field">
            <label>Чей это сайт</label>
            <UiSelect v-model="form.client" :options="clientChoices" aria-label="Клиент" />
            <input
              v-if="form.client === NEW_CLIENT"
              v-model.trim="form.clientName"
              class="ps-new-client"
              type="text"
              maxlength="120"
              placeholder="Название клиента, например «Кухни Север»"
              aria-label="Название нового клиента"
            />
            <small class="field-hint">Проекты одного клиента лежат в одной папке и делят заметки для ИИ.</small>
          </div>
        </li>

        <li class="ps-step" :class="{ 'is-done': steps[1].done }">
          <h3 class="ps-step__h"><component :is="steps[1].done ? CircleCheck : Circle" aria-hidden="true" />Главная цель Метрики</h3>
          <div v-if="goalOptions.length > 1" v-field class="field">
            <label>Что считать заявкой</label>
            <UiSelect v-model="form.goal_id" :options="goalOptions" aria-label="Цель Метрики" />
            <small class="field-hint">Заявки и их цена в сводке и отчётах считаются по этой цели.</small>
          </div>
          <p v-else class="ps-wait">
            Цели появятся после загрузки — выберем позже.
            <button
              v-if="ws.counter_row_id"
              class="btn btn--ghost btn--sm"
              type="button"
              :disabled="goalsBusy"
              @click="refreshGoals"
            >
              {{ goalsBusy ? 'Проверяем…' : 'Проверить' }}
            </button>
          </p>
        </li>

        <li class="ps-step" :class="{ 'is-done': steps[2].done }">
          <h3 class="ps-step__h"><component :is="steps[2].done ? CircleCheck : Circle" aria-hidden="true" />Цель по деньгам</h3>
          <div class="ps-pair">
            <div v-field class="field">
              <label>{{ isDrr ? 'Доля расходов на рекламу' : 'Цена заявки — не дороже' }}</label>
              <UiNumber v-model="form.kpi_target" :digits="2" :suffix="isDrr ? '%' : '₽'" placeholder="не задана" />
            </div>
            <div v-field class="field">
              <label>Бюджет на месяц</label>
              <UiNumber v-model="form.monthly_budget_rub" suffix="₽" placeholder="не задан" />
            </div>
          </div>
          <small class="field-hint">Сервис подскажет, если заявки дорожают или деньги уходят быстрее плана.</small>
        </li>

        <li class="ps-step" :class="{ 'is-done': steps[3].done }">
          <h3 class="ps-step__h"><component :is="steps[3].done ? CircleCheck : Circle" aria-hidden="true" />Шаблон ссылки</h3>
          <div v-field class="field">
            <label>Как должна выглядеть ссылка из рекламы</label>
            <input
              v-model.trim="form.url_template"
              type="text"
              inputmode="url"
              maxlength="1024"
              aria-label="Шаблон ссылки"
              :placeholder="`https://${ws.site || 'site.ru'}/?${DEFAULT_TEMPLATE_QUERY}`"
            />
            <small class="field-hint">
              {{ templateHint }} По шаблону проверяем ссылки кампаний и создаём новые объявления.
            </small>
          </div>
        </li>

        <li class="ps-step" :class="{ 'is-done': steps[4].done }">
          <h3 class="ps-step__h"><component :is="steps[4].done ? CircleCheck : Circle" aria-hidden="true" />Описание бизнеса</h3>
          <div v-field class="field">
            <label>Что продаём</label>
            <textarea
              v-model="form.product"
              rows="3"
              maxlength="2000"
              placeholder="Товары или услуги, для кого, чем отличаемся: «Кухни на заказ из массива, средний чек от 300 тыс.»"
            />
          </div>
          <div class="ps-ai">
            <button
              class="btn btn--ghost btn--sm"
              type="button"
              :disabled="draftBusy || busy || !ws.site"
              @click="readSite"
            >
              <Sparkles aria-hidden="true" />{{ draftBusy ? draftText || 'Читаем сайт…' : 'ИИ прочитает сайт' }}
            </button>
            <small class="field-hint">
              {{ ws.site ? 'Один вызов ИИ — немного месячного лимита. Запускается только по этой кнопке.' : 'У проекта не указан сайт.' }}
            </small>
          </div>
          <p v-if="draftNote" class="field-hint">{{ draftNote }}</p>
        </li>
      </ol>
      <p v-if="error" class="form-error">{{ error }}</p>
    </template>

    <template #footer>
      <button class="btn btn--ghost" type="button" :disabled="busy || draftBusy" @click="close">Пропустить</button>
      <button
        v-if="ws"
        class="btn btn--accent"
        type="button"
        :disabled="busy || draftBusy || !changed"
        @click="save"
      >
        {{ busy ? 'Сохраняем…' : 'Сохранить всё' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Circle, CircleCheck, Sparkles } from '@lucide/vue'
import Modal from './ui/Modal.vue'
import UiNumber from './ui/UiNumber.vue'
import UiSelect from './ui/UiSelect.vue'
import { errorText, jobProgressText, webApi, wsApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { loadWorkspaces, useWorkspaces } from '../composables/useWorkspaces'
import { niceQuotes } from '../utils/format'
import { DEFAULT_TEMPLATE_QUERY, suggestTemplate } from '../utils/projectSetup'

/**
 * «Подготовим проект» — сразу после «Добавить сайт» и по «Доделать» на сводке: клиент, цель Метрики,
 * цена заявки и бюджет, шаблон ссылки, описание бизнеса. Всё необязательно; «Сохранить всё» —
 * одно изменение проекта. ИИ читает сайт только по кнопке.
 */
const props = defineProps({
  open: { type: Boolean, default: false },
  wsId: { type: [Number, String], default: null },
})
const emit = defineEmits(['close', 'saved'])

const NEW_CLIENT = '__new'

const { clients, workspaces } = useWorkspaces()
const ws = ref(null)
const loading = ref(false)
const loadError = ref('')
const busy = ref(false)
const error = ref('')
const goalsBusy = ref(false)
const draftBusy = ref(false)
const draftText = ref('')
const draftNote = ref('')
// Остальные поля брифа из черновика по сайту (география, «точно не наше», заметки) — уйдут вместе с описанием.
const draftBrief = ref(null)

const form = reactive(blank())

function blank() {
  return { client: null, clientName: '', goal_id: null, kpi_target: '', monthly_budget_rub: '', url_template: '', product: '' }
}

function fromWs(w) {
  return {
    client: w.client_id ?? null,
    clientName: '',
    goal_id: w.goal_id ?? null,
    kpi_target: w.kpi_target ?? '',
    monthly_budget_rub: w.monthly_budget_rub ?? '',
    url_template: suggestTemplate(w, workspaces.value),
    product: w.brief?.product || '',
  }
}

function numOrNull(value) {
  if (value === '' || value == null) return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

const isDrr = computed(() => ws.value?.kpi_type === 'drr')

const clientChoices = computed(() => [
  { value: null, label: 'Без клиента' },
  ...clients.value
    .filter((c) => !c.archived || c.id === ws.value?.client_id)
    .map((c) => ({ value: c.id, label: niceQuotes(c.name) })),
  { value: NEW_CLIENT, label: 'Новый клиент…' },
])

const goalOptions = computed(() => [
  { value: null, label: 'Пока не выбрана' },
  ...(ws.value?.goals || []).map((g) => ({ value: g.goal_id, label: g.name, hint: `№ ${g.goal_id}` })),
])

const templateHint = computed(() => {
  if (ws.value?.url_template) return 'Шаблон уже задан.'
  const fromOther = workspaces.value.some((o) => o.id !== ws.value?.id && o.url_template)
  return fromOther ? 'Адрес — этого сайта, метки — как в вашем прошлом проекте.' : 'Адрес — этого сайта, метки — обычные для Директа.'
})

const steps = computed(() => [
  { key: 'client', done: form.client === NEW_CLIENT ? !!form.clientName : form.client != null },
  { key: 'goal', done: form.goal_id != null },
  { key: 'kpi', done: numOrNull(form.kpi_target) > 0 || numOrNull(form.monthly_budget_rub) > 0 },
  { key: 'template', done: !!form.url_template },
  { key: 'brief', done: !!form.product.trim() },
])
const doneCount = computed(() => steps.value.filter((s) => s.done).length)

/** Что изменилось относительно проекта — одним PATCH; клиента «Новый…» создаём перед ним. */
const payload = computed(() => {
  const w = ws.value
  if (!w) return {}
  const out = {}
  if (form.client !== NEW_CLIENT && (form.client ?? null) !== (w.client_id ?? null)) out.client_id = form.client
  if ((form.goal_id ?? null) !== (w.goal_id ?? null)) out.goal_id = form.goal_id
  const target = numOrNull(form.kpi_target)
  if (target !== (w.kpi_target ?? null)) {
    out.kpi_type = target === null ? '' : w.kpi_type || 'cpa'
    out.kpi_target = target
  }
  const budget = numOrNull(form.monthly_budget_rub)
  if (budget !== (w.monthly_budget_rub ?? null)) out.monthly_budget_rub = budget
  if (form.url_template !== (w.url_template || '')) out.url_template = form.url_template
  const brief = w.brief || {}
  const product = form.product.trim()
  if (product !== (brief.product || '') || draftBrief.value) {
    const merged = { ...brief, product }
    // Черновик заполняет только пустые поля брифа — написанное руками не трогаем.
    for (const [key, value] of Object.entries(draftBrief.value || {})) {
      if (key === 'product') continue
      const empty = Array.isArray(merged[key]) ? !merged[key].length : !merged[key]
      if (empty && value && (!Array.isArray(value) || value.length)) merged[key] = value
    }
    out.brief = merged
  }
  return out
})
const changed = computed(
  () => Object.keys(payload.value).length > 0 || (form.client === NEW_CLIENT && !!form.clientName),
)

watch(
  () => [props.open, props.wsId],
  ([open]) => {
    if (open) load()
  },
  { immediate: true },
)

async function load() {
  if (!props.wsId) return
  loading.value = true
  loadError.value = ''
  error.value = ''
  draftNote.value = ''
  draftBrief.value = null
  ws.value = null
  try {
    const [w] = await Promise.all([wsApi.get(props.wsId), loadWorkspaces().catch(() => null)])
    ws.value = w
    Object.assign(form, blank(), fromWs(w))
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

/** Цели приходят с первой загрузкой Метрики — пока окно открыто, их можно перепроверить. */
async function refreshGoals() {
  goalsBusy.value = true
  try {
    const w = await wsApi.get(props.wsId)
    ws.value = { ...ws.value, goals: w.goals || [] }
    if (!w.goals?.length) pushToast('Цели ещё загружаются — выберете их позже на сводке проекта')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    goalsBusy.value = false
  }
}

async function readSite() {
  draftBusy.value = true
  draftText.value = ''
  draftNote.value = ''
  error.value = ''
  try {
    const res = await wsApi.briefDraft(ws.value.id, ws.value.site || '', (job) => {
      draftText.value = jobProgressText(job)
    })
    const brief = res.brief || {}
    draftBrief.value = brief
    const kept = !!form.product.trim()
    if (!kept && brief.product) form.product = brief.product
    const where = res.title ? `«${res.title}»` : res.site || 'сайту'
    draftNote.value = kept
      ? `ИИ прочитал ${where}. Ваше описание оставили как есть; география и «точно не наше» добавятся в бриф, если там пусто.`
      : `Описание составлено по ${where} — проверьте и поправьте. География и «точно не наше» тоже добавятся в бриф.`
  } catch (err) {
    error.value = errorText(err)
  } finally {
    draftBusy.value = false
  }
}

async function save() {
  error.value = ''
  const target = numOrNull(form.kpi_target)
  if (target !== null && target <= 0) {
    error.value = isDrr.value ? 'Доля расходов — число больше нуля.' : 'Цена заявки — число больше нуля.'
    return
  }
  const budget = numOrNull(form.monthly_budget_rub)
  if (budget !== null && budget < 0) {
    error.value = 'Бюджет на месяц не может быть меньше нуля.'
    return
  }
  busy.value = true
  try {
    if (form.client === NEW_CLIENT && form.clientName) {
      const created = await webApi.createClient({ name: form.clientName })
      // Повторное «Сохранить» после ошибки не создаст второго клиента.
      form.client = created.id
      form.clientName = ''
    }
    const body = { ...payload.value }
    const updated = Object.keys(body).length ? await wsApi.update(ws.value.id, body) : ws.value
    ws.value = updated
    if ('client_id' in body) await loadWorkspaces(true).catch(() => null)
    pushToast(doneCount.value === steps.value.length ? 'Проект настроен' : 'Сохранено. Остальное можно доделать на сводке проекта', 'ok')
    emit('saved', updated)
    emit('close')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

function close() {
  if (!busy.value && !draftBusy.value) emit('close')
}
</script>

<style scoped>
.ps-lead {
  margin: 0 0 14px;
}
.ps-count {
  display: block;
  margin-top: 6px;
}
.ps-steps {
  display: grid;
  gap: 14px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.ps-step {
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
}
.ps-step__h {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  font-size: 15px;
}
.ps-step__h svg {
  flex: none;
  width: 18px;
  height: 18px;
  color: var(--faint);
}
.ps-step.is-done .ps-step__h svg {
  color: var(--ok);
}
.ps-step .field {
  margin: 0;
}
.ps-new-client {
  margin-top: 8px;
}
.ps-pair {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px 14px;
  margin-bottom: 6px;
}
.ps-wait {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 12px;
  margin: 0;
  color: var(--muted);
}
.ps-ai {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 12px;
  margin-top: 8px;
}
</style>
