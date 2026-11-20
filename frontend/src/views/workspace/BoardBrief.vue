<template>
  <div class="board-brief">
    <p v-if="intro" class="board-section__intro">{{ intro }}</p>

    <div class="board-brief__fill">
      <button class="btn btn--ghost" type="button" :disabled="disabled || filling || !fill" @click="fillFromSite">
        {{ filling ? 'ИИ читает сайт…' : '✦ ИИ заполнит по сайту' }}
      </button>
      <small class="hint">
        Один короткий запрос — спишется из лимита. ИИ заполнит только пустые поля, сохранить — вам.
      </small>
    </div>

    <div class="form-grid">
      <div v-if="brief.accounts.length > 1" v-field class="field" :class="{ 'is-missing': lacks('кабинет') }">
        <label>Кабинет Директа</label>
        <select v-model="form.account_id" :disabled="disabled">
          <option :value="null" disabled>Выберите кабинет</option>
          <option v-for="a in brief.accounts" :key="a.id" :value="a.id">{{ a.name }}</option>
        </select>
      </div>
      <div v-field class="field field--wide" :class="{ 'is-missing': lacks('название кампании') }">
        <label>Название кампании</label>
        <input v-model.trim="form.name" type="text" maxlength="255" :disabled="disabled" placeholder="Поиск — Кухни на заказ — Казань" />
      </div>
      <div v-field class="field field--wide" :class="{ 'is-missing': lacks('посадочная ссылка') }">
        <label>Сайт — посадочная страница</label>
        <input v-model.trim="form.href" type="url" maxlength="1024" :disabled="disabled" :placeholder="site || 'https://…'" />
        <small v-if="brief.link_template && !form.href" class="field-hint">
          Пусто — объявления поведут по шаблону ссылки проекта.
        </small>
      </div>
      <div v-field class="field field--wide">
        <label>Что рекламируем</label>
        <textarea v-model="form.product" rows="2" maxlength="300" :disabled="disabled" placeholder="Товар или услуга, для кого, оффер" />
      </div>
      <div v-field class="field field--wide" :class="{ 'is-missing': lacks('регион') }">
        <label>Регион показа</label>
        <RegionPicker v-model="form.region_ids" :selected="regions" />
        <small v-if="brief.region_text && !form.region_ids.length" class="field-hint">
          ИИ записал словами: «{{ brief.region_text }}» — выберите регионы из справочника.
        </small>
      </div>
      <div v-field class="field">
        <label>Бюджет в неделю, ₽</label>
        <input v-model.number="form.weekly_budget_rub" type="number" min="0" step="100" :disabled="disabled" placeholder="Например, 10 000" />
      </div>
      <div v-field class="field">
        <label>Цель — конверсия</label>
        <select v-model="form.goal_id" :disabled="disabled || !brief.goals.length">
          <option :value="null">{{ brief.goals.length ? 'Без цели — оптимизировать клики' : 'Целей Метрики нет' }}</option>
          <option v-for="g in brief.goals" :key="g.goal_id" :value="g.goal_id">{{ g.name }}</option>
        </select>
      </div>
    </div>

    <details class="board-brief__limits" :open="hasLimits">
      <summary>Ограничения — запрещённые слова и обязательные минимумы</summary>
      <div class="form-grid">
        <div v-field class="field field--wide">
          <label>Запрещённые слова — через запятую</label>
          <input v-model="banned" type="text" :disabled="disabled" placeholder="дешёвый, бесплатно" />
        </div>
        <div v-for="c in COUNTS" :key="c.key" v-field class="field board-brief__count">
          <label>{{ c.label }}</label>
          <input v-model.number="form[c.key]" type="number" min="0" :max="c.max" :disabled="disabled" placeholder="—" />
        </div>
        <div v-field class="field field--wide">
          <label>Другие ограничения — по одному на строку</label>
          <textarea v-model="form.other" rows="2" maxlength="600" :disabled="disabled" />
        </div>
      </div>
    </details>

    <ul v-if="brief.extra.length" class="board-brief__extra" aria-label="Ещё в брифе от ИИ">
      <li v-for="(e, i) in brief.extra" :key="i">
        <span>{{ e.text }}</span><small v-if="e.value">{{ e.value }}</small>
      </li>
    </ul>

    <div class="board-brief__save">
      <p v-if="missing.length" class="hint tone-bad">Чтобы идти дальше, заполните: {{ missing.join(', ') }}.</p>
      <button v-if="dirty" class="btn btn--ghost" type="button" :disabled="disabled || saving" @click="save">
        {{ saving ? 'Сохраняем…' : 'Сохранить бриф' }}
      </button>
      <small v-if="dirty" class="hint">«Дальше» тоже сохранит.</small>
    </div>
  </div>
</template>

<script setup>
/**
 * Шаг «Бриф» мастера доски — формой (пункт 6 плана доски). Форма — ждущая таблица брифа: «Сохранить» кладёт поля
 * в неё на сервере (save), «ИИ заполнит по сайту» — один лёгкий вызов модели, им заполняются только пустые поля.
 * Родитель перед «Дальше» зовёт save(), если форма менялась, и блокирует кнопку, пока есть missing.
 */
import { computed, reactive, ref, watch } from 'vue'
import RegionPicker from '../../components/RegionPicker.vue'

const props = defineProps({
  // build.brief: { action_id, form, missing, region_text, regions, accounts, goals, link_template, extra }
  brief: { type: Object, required: true },
  intro: { type: String, default: '' },
  site: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  // async (form) => true, если сохранилось.
  saveForm: { type: Function, required: true },
  // async (site) => { form, regions } или null при ошибке.
  fill: { type: Function, default: null },
})

const COUNTS = [
  { key: 'titles', label: 'Заголовков в группе, не меньше', max: 7 },
  { key: 'texts', label: 'Текстов в группе', max: 3 },
  { key: 'keywords', label: 'Фраз в группе', max: 200 },
  { key: 'sitelinks', label: 'Быстрых ссылок', max: 8 },
  { key: 'callouts', label: 'Уточнений', max: 50 },
]

const form = reactive({})
const banned = ref('')
const regions = ref([])
const filling = ref(false)
const saving = ref(false)
let baseline = ''

function snapshot() {
  return JSON.stringify({ ...form, banned_words: bannedList() })
}

function bannedList() {
  return banned.value.split(/[,;\n]/).map((w) => w.trim()).filter(Boolean)
}

function reset(brief) {
  const f = brief.form || {}
  Object.assign(form, {
    account_id: f.account_id ?? null,
    name: f.name || '',
    href: f.href || '',
    product: f.product || '',
    region_ids: [...(f.region_ids || [])],
    weekly_budget_rub: f.weekly_budget_rub ?? null,
    goal_id: f.goal_id ?? null,
    other: f.other || '',
    ...Object.fromEntries(COUNTS.map((c) => [c.key, f[c.key] ?? null])),
  })
  banned.value = (f.banned_words || []).join(', ')
  regions.value = brief.regions || []
  baseline = snapshot()
}

// Сервер прислал новую форму (сохранение, ход ИИ) — показываем её; свои несохранённые правки при этом теряются,
// только если форма на сервере правда поменялась.
watch(() => JSON.stringify(props.brief.form), () => reset(props.brief), { immediate: true })

const dirty = computed(() => snapshot() !== baseline)
const hasLimits = computed(() => !!(banned.value || form.other || COUNTS.some((c) => form[c.key])))

// Чего не хватает — по полям формы, а не по последнему сохранению: заполнили — «Дальше» сразу доступна.
const missing = computed(() => {
  const out = []
  if (!form.account_id) out.push('кабинет')
  if (!form.name) out.push('название кампании')
  if (!form.region_ids.some((id) => id > 0)) out.push('регион')
  if (!form.href && !props.brief.link_template) out.push('посадочная ссылка')
  return out
})

const lacks = (what) => missing.value.includes(what)

async function save() {
  if (saving.value) return false
  saving.value = true
  try {
    const ok = await props.saveForm({ ...form, banned_words: bannedList() })
    if (ok) baseline = snapshot()
    return ok
  } finally {
    saving.value = false
  }
}

async function fillFromSite() {
  if (!props.fill || filling.value) return
  filling.value = true
  try {
    const res = await props.fill(form.href || props.site)
    if (!res) return
    const got = res.form || {}
    for (const key of ['name', 'href', 'product', 'weekly_budget_rub', 'other']) {
      if (got[key] && !form[key]) form[key] = got[key]
    }
    if (got.region_ids?.length && !form.region_ids.length) {
      regions.value = [...regions.value, ...(res.regions || [])]
      form.region_ids = [...got.region_ids]
    }
    if (got.banned_words?.length && !banned.value) banned.value = got.banned_words.join(', ')
  } finally {
    filling.value = false
  }
}

defineExpose({ save, dirty, missing })
</script>
