<template>
  <section id="protect" class="panel">
    <div class="panel__head">
      <div>
        <h2>Защита бюджета</h2>
        <p class="hint">
          Раз в сутки сервис проверяет кампании проекта. Сообщение приходит в колокольчик и в Telegram, остановка
          попадает в «Историю» — там её можно вернуть.
        </p>
      </div>
    </div>

    <UiSkeleton v-if="!state && !error" variant="text" :count="3" label="Загружаем защиту бюджета" />
    <p v-else-if="error && !state" class="form-error">{{ error }}</p>
    <template v-else>
      <p v-if="!state.allowed" class="notice notice--warn">
        Защита бюджета — на тарифе выше. <RouterLink to="/cabinet/plan">Тарифы</RouterLink>
      </p>
      <p v-else-if="state.target_cpa_rub == null" class="notice">
        Цена заявки не задана, поэтому порог — только в рублях. Задайте «Главный показатель» — «Цена заявки» в
        блоке «Цель и бюджет»: тогда порог можно задать в ценах заявки, и он подойдёт этому клиенту сам.
        <button class="link-btn" type="button" @click="$emit('set-target')">Задать цену заявки</button>
      </p>

      <div class="prot">
        <div v-for="item in state.items" :key="item.key" class="prot__row">
          <UiSwitch
            :model-value="item.enabled"
            :disabled="busy === item.key || (!state.allowed && !item.enabled)"
            @update:model-value="toggle(item, $event)"
          >
            <b>{{ item.title }}</b>
            <small>{{ item.caption }}</small>
          </UiSwitch>

          <div v-if="item.money" class="prot__thr">
            <span class="prot__label">Порог</span>
            <div class="seg" role="group" :aria-label="`Порог: ${item.title}`">
              <button
                type="button"
                :class="{ active: draft[item.key].kind === 'cpa_x' }"
                :aria-pressed="draft[item.key].kind === 'cpa_x'"
                :disabled="state.target_cpa_rub == null && draft[item.key].kind !== 'cpa_x'"
                v-tip="state.target_cpa_rub == null ? 'Сначала задайте цену заявки' : ''"
                @click="setKind(item, 'cpa_x')"
              >
                В ценах заявки
              </button>
              <button
                type="button"
                :class="{ active: draft[item.key].kind === 'rub' }"
                :aria-pressed="draft[item.key].kind === 'rub'"
                @click="setKind(item, 'rub')"
              >
                В рублях
              </button>
            </div>
            <UiNumber
              v-model="draft[item.key].value"
              class="prot__num"
              :digits="draft[item.key].kind === 'cpa_x' ? 1 : 0"
              :suffix="draft[item.key].kind === 'cpa_x' ? '×' : '₽'"
              :aria-label="draft[item.key].kind === 'cpa_x' ? 'Во сколько раз больше цены заявки' : 'Порог, ₽'"
            />
            <small v-if="draft[item.key].kind === 'cpa_x'" class="prot__calc">{{ calcText(item) }}</small>
            <button
              v-if="changed(item)"
              class="btn btn--ghost btn--sm"
              type="button"
              :disabled="busy === item.key || !state.allowed"
              @click="saveThreshold(item)"
            >
              {{ busy === item.key ? 'Сохраняем…' : 'Сохранить порог' }}
            </button>
          </div>
          <p v-if="item.needs_target" class="prot__warn tone-warn">
            Цена заявки больше не задана — проверка не работает. Задайте цену заявки или переключите порог на рубли.
          </p>
        </div>
      </div>
      <p v-if="error" class="form-error">{{ error }}</p>
    </template>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { errorText, protectApi } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import UiNumber from '../../components/ui/UiNumber.vue'
import UiSkeleton from '../../components/ui/UiSkeleton.vue'
import UiSwitch from '../../components/ui/UiSwitch.vue'
import { rub } from '../../utils/format'
import { useWs } from './context'

/**
 * «Защита бюджета» в настройках проекта (обычный режим): два-три переключателя из шаблонов правил.
 * Порог расхода — в рублях или «в N раз больше цены заявки»: рубли сервис считает при каждой проверке.
 */
defineEmits(['set-target'])

const { ws } = useWs()
const state = ref(null)
const error = ref('')
const busy = ref('')
// Порог в форме: { [key]: { kind, value } } — пока не сохранён, отличается от state.
const draft = reactive({})

// savedKey — строка, которую только что сохранили: у неё форма берёт порог с сервера. У остальных
// несохранённые правки порога остаются (их сбросил бы ответ на включение соседнего переключателя).
function fill(next, savedKey = null) {
  const prev = Object.fromEntries((state.value?.items || []).map((i) => [i.key, i]))
  state.value = next
  for (const item of next.items) {
    const old = prev[item.key]
    const keep = savedKey && item.key !== savedKey && old && draft[item.key] && changed(old)
    if (!keep) draft[item.key] = { kind: item.threshold_kind, value: item.value }
  }
}

async function load() {
  error.value = ''
  try {
    fill(await protectApi.get(ws.value.id))
  } catch (err) {
    error.value = errorText(err)
  }
}

function changed(item) {
  const d = draft[item.key]
  return d.kind !== item.threshold_kind || Number(d.value) !== Number(item.value)
}

function setKind(item, kind) {
  const d = draft[item.key]
  if (d.kind === kind) return
  const cpa = state.value.target_cpa_rub
  // Переводим порог в новые единицы, чтобы сумма осталась примерно той же.
  if (kind === 'rub') d.value = cpa ? Math.round((Number(d.value) || 0) * cpa) : item.key === 'spend_pause' ? 5000 : 3000
  else d.value = cpa ? Math.max(0.5, Math.round(((Number(d.value) || 0) / cpa) * 10) / 10) : 2
  d.kind = kind
}

function calcText(item) {
  const cpa = state.value.target_cpa_rub
  const x = Number(draft[item.key].value) || 0
  if (!cpa) return 'цена заявки не задана'
  return `${rub(Math.round(x * cpa) * 100)} при цене заявки ${rub(cpa * 100)}`
}

async function send(item, payload, done) {
  busy.value = item.key
  error.value = ''
  try {
    fill(await protectApi.set(ws.value.id, item.key, payload), item.key)
    if (done) pushToast(done, 'ok')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = ''
  }
}

function toggle(item, on) {
  // Включают с порогом из формы: его могли поменять до включения.
  const d = draft[item.key]
  const payload = item.money && changed(item) ? { enabled: on, threshold_kind: d.kind, value: d.value } : { enabled: on }
  return send(item, payload, on ? 'Проверка включена' : 'Проверка выключена')
}

function saveThreshold(item) {
  const d = draft[item.key]
  if (!(Number(d.value) > 0)) {
    error.value = 'Укажите порог больше нуля.'
    return
  }
  return send(item, { enabled: item.enabled, threshold_kind: d.kind, value: d.value }, 'Порог сохранён')
}

// Цену заявки поменяли в «Цель и бюджет» — пересчитываем пороги и подсказки.
watch(
  () => [ws.value.id, ws.value.kpi_type, ws.value.kpi_target, ws.value.client_id],
  () => load(),
)
onMounted(load)
</script>

<style scoped>
.prot {
  display: grid;
  gap: 0;
}
.prot__row {
  display: grid;
  gap: 6px;
  padding: 14px 0;
  border-top: 1px solid var(--line);
}
.prot__row:first-child {
  border-top: 0;
  padding-top: 4px;
}
.prot__thr {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin-left: 46px;
}
.prot__label {
  color: var(--muted);
  font-size: 13px;
}
.prot__num {
  width: 120px;
}
.prot__calc {
  color: var(--faint);
  font-size: 13px;
}
.prot__warn {
  margin: 0 0 0 46px;
  font-size: 13px;
}
.link-btn {
  text-decoration: underline;
  text-underline-offset: 3px;
}
@media (max-width: 640px) {
  .prot__thr,
  .prot__warn {
    margin-left: 0;
  }
}
</style>
