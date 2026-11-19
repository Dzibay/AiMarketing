<template>
  <form class="panel" @submit.prevent="save">
    <h2>Для новых проектов</h2>
    <p class="hint">
      Так настроится каждый новый проект (сайт). Уже созданные проекты не меняются. Не заданное здесь остаётся как
      обычно.
    </p>
    <UiSkeleton v-if="!meta && !error" variant="text" :count="4" label="Загружаем настройки" />
    <p v-else-if="!meta" class="form-error">{{ error }}</p>
    <template v-else>
      <h3 class="subhead">{{ expert ? 'KPI' : 'Главный показатель' }}</h3>
      <div class="pd-row">
        <UiSelect v-model="draft.kpi_type" class="pd-kpi" :options="kpiOptions" aria-label="Главный показатель" />
        <UiNumber
          v-model="draft.kpi_target"
          class="pd-num"
          :digits="draft.kpi_type === 'drr' ? 1 : 0"
          :suffix="draft.kpi_type === 'drr' ? '%' : draft.kpi_type === 'cpa' ? '₽' : ''"
          :disabled="!draft.kpi_type"
          aria-label="Целевое значение"
        />
      </div>

      <template v-if="models?.configured && models.items.length > 1">
        <h3 class="subhead">Какой моделью отвечать</h3>
        <p class="hint">Чем больше точек, тем умнее ответ и тем больше лимита он тратит.</p>
        <div class="seg pd-seg" role="group" aria-label="Модель для новых проектов">
          <button
            v-for="m in models.items"
            :key="m.id"
            type="button"
            :class="{ active: modelPick === m.id }"
            :aria-pressed="modelPick === m.id"
            @click="draft.model_class = m.id === models.default ? '' : m.id"
          >
            {{ m.name }} <span class="pd-dots" aria-hidden="true">{{ dots(m.dots) }}</span>
          </button>
        </div>
      </template>

      <h3 class="subhead">Защита бюджета</h3>
      <p v-if="!meta.protection_allowed" class="notice notice--warn">
        Защита бюджета — на тарифе выше. <RouterLink to="/cabinet/plan">Тарифы</RouterLink>
      </p>
      <p v-else class="hint">Если у проекта нет цены заявки, порог «в ценах заявки» заменится рублёвым.</p>
      <div class="pd-prot">
        <div v-for="item in meta.protection" :key="item.key" class="pd-prot__row">
          <UiSwitch v-model="draft.protection[item.key].enabled" :disabled="!meta.protection_allowed">
            <b>{{ item.title }}</b>
            <small>{{ item.caption }}</small>
          </UiSwitch>
          <div v-if="item.money && draft.protection[item.key].enabled" class="pd-row pd-prot__thr">
            <div class="seg" role="group" :aria-label="`Порог: ${item.title}`">
              <button
                type="button"
                :class="{ active: draft.protection[item.key].threshold_kind === 'cpa_x' }"
                @click="setKind(item, 'cpa_x')"
              >
                В ценах заявки
              </button>
              <button
                type="button"
                :class="{ active: draft.protection[item.key].threshold_kind === 'rub' }"
                @click="setKind(item, 'rub')"
              >
                В рублях
              </button>
            </div>
            <UiNumber
              v-model="draft.protection[item.key].value"
              class="pd-num"
              :digits="draft.protection[item.key].threshold_kind === 'cpa_x' ? 1 : 0"
              :suffix="draft.protection[item.key].threshold_kind === 'cpa_x' ? '×' : '₽'"
              aria-label="Порог"
            />
          </div>
        </div>
      </div>

      <template v-if="expert">
        <h3 class="subhead">Шаблон ссылки</h3>
        <p class="hint">Метки для ссылок в объявлениях. Домен подставится сам — сайт нового проекта.</p>
        <div v-field class="field">
          <input
            v-model.trim="draft.url_template"
            type="text"
            spellcheck="false"
            aria-label="Шаблон ссылки"
            placeholder="?utm_source=yandex&utm_medium=cpc&utm_campaign={campaign_id}"
          />
        </div>

        <h3 class="subhead">Доступ ИИ</h3>
        <UiSwitch v-model="draft.access_set">
          <b>Задать доступ ИИ</b>
          <small>Выключено — как обычно: всё ждёт вашего «Выполнить».</small>
        </UiSwitch>
        <div v-if="draft.access_set" class="pd-access">
          <UiCheckbox v-model="draft.access.full_access" class="check-row">
            <strong>Полный доступ</strong>
            <small>ИИ выполняет любые действия без согласования. Удаление — тоже.</small>
          </UiCheckbox>
          <fieldset class="pd-access__cats" :disabled="draft.access.full_access">
            <legend>Без согласования разрешено</legend>
            <UiCheckbox v-for="c in meta.ai_categories" :key="c.key" v-model="draft.access.categories[c.key]" class="check-row">
              {{ c.label }}
            </UiCheckbox>
          </fieldset>
          <div v-field class="field pd-pct">
            <label>Предел разового изменения бюджета или ставки, %</label>
            <UiNumber v-model="draft.access.max_change_pct" suffix="%" :disabled="draft.access.full_access" aria-label="Предел, %" />
          </div>
        </div>
      </template>

      <p v-if="error" class="form-error">{{ error }}</p>
      <div class="form-actions">
        <button class="btn btn--accent" type="submit" :disabled="busy || !changed">
          {{ busy ? 'Сохраняем…' : 'Сохранить' }}
        </button>
      </div>
    </template>
  </form>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { aiApi, defaultsApi, errorText } from '../api/web'
import { askConfirm } from '../composables/useConfirm'
import { useExpert } from '../composables/useExpert'
import { pushToast } from '../composables/useToasts'
import UiCheckbox from './ui/UiCheckbox.vue'
import UiNumber from './ui/UiNumber.vue'
import UiSelect from './ui/UiSelect.vue'
import UiSkeleton from './ui/UiSkeleton.vue'
import UiSwitch from './ui/UiSwitch.vue'

/**
 * Настройки → «Для новых проектов»: главный показатель, модель, защита бюджета, а в экспертном режиме — шаблон
 * ссылки и доступ ИИ. Сервис ставит их каждому новому проекту; уже созданные не трогает.
 */
const { expert } = useExpert()
const meta = ref(null)
const models = ref(null)
const error = ref('')
const busy = ref(false)
const draft = reactive({ kpi_type: '', kpi_target: null, model_class: '', url_template: '', access_set: false, access: {}, protection: {} })
const saved = ref('')

const kpiOptions = computed(() => [
  { value: '', label: 'Не задавать' },
  { value: 'cpa', label: expert.value ? 'CPA — цена конверсии' : 'Цена заявки' },
  { value: 'drr', label: expert.value ? 'ДРР — доля рекламных расходов' : 'Доля расходов на рекламу' },
])
// Класс, который админ выключил, показываем как «по умолчанию».
const modelPick = computed(() => {
  const m = models.value
  if (!m) return ''
  return m.items.some((c) => c.id === draft.model_class) ? draft.model_class : m.default
})

function dots(n) {
  const k = Math.max(1, Math.min(3, Number(n) || 1))
  return '●'.repeat(k) + '○'.repeat(3 - k)
}

function fill(res) {
  meta.value = res
  const d = res.defaults
  draft.kpi_type = d.kpi?.kpi_type || ''
  draft.kpi_target = d.kpi?.kpi_target ?? null
  draft.model_class = d.model_class || ''
  draft.url_template = d.url_template || ''
  draft.access_set = !!d.ai_access
  const access = d.ai_access || res.default_ai_access
  draft.access = { full_access: !!access.full_access, categories: { ...(access.categories || {}) }, max_change_pct: access.max_change_pct ?? 30 }
  const prot = d.budget_protection || {}
  draft.protection = Object.fromEntries(
    res.protection.map((p) => {
      const cur = prot[p.key] || {}
      const kind = cur.threshold_kind || (p.default_x ? 'cpa_x' : 'rub')
      const value = cur.value ?? (kind === 'cpa_x' ? p.default_x : p.default_rub)
      return [p.key, { enabled: !!cur.enabled, threshold_kind: kind, value }]
    }),
  )
  saved.value = JSON.stringify(payload())
}

function setKind(item, kind) {
  const p = draft.protection[item.key]
  if (p.threshold_kind === kind) return
  p.threshold_kind = kind
  p.value = kind === 'cpa_x' ? item.default_x || 2 : item.default_rub || 3000
}

function payload() {
  const protection = {}
  for (const p of meta.value.protection) {
    const d = draft.protection[p.key]
    if (!d.enabled) continue
    protection[p.key] = p.money ? { enabled: true, threshold_kind: d.threshold_kind, value: d.value } : { enabled: true }
  }
  const out = {
    kpi: draft.kpi_type ? { kpi_type: draft.kpi_type, kpi_target: draft.kpi_target || null } : null,
    budget_protection: Object.keys(protection).length ? protection : null,
  }
  // Модель — только если её поменяли: класс, который админ потом выключил, не мешает сохранить остальное.
  if (draft.model_class !== (meta.value.defaults.model_class || '')) out.model_class = draft.model_class
  // В обычном режиме шаблон ссылки и доступ ИИ не видны — их не трогаем.
  if (expert.value) {
    out.url_template = draft.url_template
    out.ai_access = draft.access_set ? { ...draft.access, categories: { ...draft.access.categories } } : null
  }
  return out
}

const changed = computed(() => !!meta.value && JSON.stringify(payload()) !== saved.value)

async function save() {
  error.value = ''
  if (draft.kpi_type && !(Number(draft.kpi_target) > 0)) {
    error.value = 'Укажите целевое значение больше нуля или выберите «Не задавать».'
    return
  }
  const body = payload()
  if (body.ai_access?.full_access && !meta.value.defaults.ai_access?.full_access) {
    const ok = await askConfirm('Дать ИИ полный доступ во всех новых проектах? Он сможет менять бюджеты и ставки без вашего подтверждения.')
    if (!ok) return
  }
  busy.value = true
  try {
    fill(await defaultsApi.save(body))
    pushToast('Сохранено — так настроятся новые проекты', 'ok')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try {
    const [res, m] = await Promise.all([defaultsApi.get(), aiApi.models().catch(() => null)])
    models.value = m
    fill(res)
  } catch (err) {
    error.value = errorText(err)
  }
})
</script>

<style scoped>
.subhead {
  margin: 22px 0 6px;
  font-size: 15px;
}
.pd-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
}
.pd-kpi {
  min-width: 220px;
}
.pd-num {
  width: 140px;
}
.pd-seg {
  max-width: 100%;
  overflow-x: auto;
}
.pd-dots {
  margin-left: 4px;
  font-size: 10px;
  letter-spacing: 1px;
  opacity: 0.7;
}
.pd-prot__row {
  display: grid;
  gap: 6px;
  padding: 12px 0;
  border-top: 1px solid var(--line);
}
.pd-prot__row:first-child {
  border-top: 0;
}
.pd-prot__thr {
  margin-left: 46px;
}
.pd-access {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}
.pd-access__cats {
  display: grid;
  gap: 4px;
  border: 0;
  padding: 0;
  margin: 0;
}
.pd-pct {
  max-width: 260px;
}
@media (max-width: 640px) {
  .pd-prot__thr {
    margin-left: 0;
  }
}
</style>
