<template>
  <section class="panel">
    <div class="panel__head">
      <h2>Тарифы</h2>
      <button class="btn btn--accent btn--xs" type="button" @click="openNew">Добавить тариф</button>
    </div>
    <p class="hint">
      Лимит единиц действует на оплаченный период (срок тарифа) и сгорает в его конце; пусто — без ограничений.
      Пользователю единицы не показываются — только доля лимита. Пробный тариф выдаётся новым пользователям при
      регистрации, он один. Выключенный тариф не предлагается, но у тех, кто на нём, продолжает действовать.
    </p>
    <p v-if="loading" class="empty">Загружаем…</p>
    <p v-else-if="!items.length" class="empty">Тарифов пока нет — все пользователи работают без ограничений.</p>
    <div v-else class="table-scroll">
      <table class="table admin-plans">
        <thead>
          <tr>
            <th>Тариф</th>
            <th class="num">Цена</th>
            <th class="num">Дней</th>
            <th class="num">Единиц за период</th>
            <th class="num">Проектов</th>
            <th>Нет в тарифе</th>
            <th class="num">Пользователей</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in items" :key="p.id" :class="{ 'is-muted': !p.active }">
            <td>
              <strong>{{ p.name }}</strong>
              <span v-if="p.is_trial" class="chip chip--ok">пробный</span>
              <span v-if="!p.active" class="chip chip--info">выключен</span>
            </td>
            <td class="num">{{ p.price_rub ? rubles(p.price_rub) : 'бесплатно' }}</td>
            <td class="num">{{ num(p.days) }}</td>
            <td class="num">{{ p.units_limit === null ? 'без лимита' : num(p.units_limit, 2) }}</td>
            <td class="num">{{ p.projects_limit === null ? 'без лимита' : num(p.projects_limit) }}</td>
            <td>{{ missingFeatures(p) || '—' }}</td>
            <td class="num">{{ num(p.users) }}</td>
            <td>
              <button class="btn btn--ghost btn--xs" type="button" @click="openEdit(p)">Изменить</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <section v-if="targets.length && items.length > 1" class="panel">
    <div class="panel__head">
      <h2>Скидки за переход</h2>
    </div>
    <p class="hint">
      Процент от цены нового тарифа, когда пользователь переходит на него со своего. Пусто или 0 — без скидки.
      Пользователь видит цену со скидкой в окне «Лимит исчерпан» и на странице тарифа.
    </p>
    <div class="table-scroll">
      <table class="table admin-upgrades">
        <thead>
          <tr>
            <th>С тарифа ↓ &nbsp;на тариф →</th>
            <th v-for="to in targets" :key="to.id" class="num">{{ to.name }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="from in items" :key="from.id">
            <th scope="row">{{ from.name }}</th>
            <td v-for="to in targets" :key="to.id" class="num">
              <span v-if="from.id === to.id" class="cell-note">—</span>
              <input
                v-else
                type="number"
                min="0"
                max="100"
                step="any"
                inputmode="decimal"
                placeholder="0"
                :aria-label="`Скидка с «${from.name}» на «${to.name}», %`"
                :value="discount(from.id, to.id)"
                :disabled="savingCell === cellKey(from.id, to.id)"
                @change="saveDiscount(from, to, $event.target)"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <Modal :open="!!form" :title="form?.id ? `Тариф «${form.name || '…'}»` : 'Новый тариф'" :locked="busy" @close="form = null">
    <form v-if="form" id="plan-form" class="form-grid" @submit.prevent="save">
      <p v-if="error" class="form-error">{{ error }}</p>
      <div v-field class="field field--wide">
        <label>Название</label>
        <input v-model="form.name" type="text" maxlength="60" required placeholder="Например, Старт" />
      </div>
      <div v-field class="field">
        <label>Цена, ₽</label>
        <input v-model="form.price_rub" type="number" min="0" step="any" inputmode="decimal" placeholder="0" />
      </div>
      <div v-field class="field">
        <label>Срок, дней</label>
        <input v-model="form.days" type="number" min="1" max="3660" step="1" inputmode="numeric" required />
        <small class="field-hint">Это и срок подписки, и период лимита.</small>
      </div>
      <div v-field class="field">
        <label>Единиц за период</label>
        <input v-model="form.units_limit" type="number" min="0" step="any" inputmode="decimal" placeholder="без лимита" />
        <small class="field-hint">ИИ, Вордстат и картинки. 0 — платные функции закрыты.</small>
      </div>
      <div v-field class="field">
        <label>Проектов</label>
        <input v-model="form.projects_limit" type="number" min="0" step="1" inputmode="numeric" placeholder="без лимита" />
        <small class="field-hint">Проект — счётчик Метрики с его кампаниями.</small>
      </div>
      <div v-field class="field">
        <label>Скидка за 3 срока, %</label>
        <input v-model="form.discount_3_pct" type="number" min="0" max="99" step="any" inputmode="decimal" placeholder="0" />
      </div>
      <div v-field class="field">
        <label>Скидка за 12 сроков, %</label>
        <input v-model="form.discount_12_pct" type="number" min="0" max="99" step="any" inputmode="decimal" placeholder="0" />
        <small class="field-hint">Пользователь может оплатить сразу 1, 3 или 12 сроков (месяцев) — скидка к сумме.</small>
      </div>
      <fieldset>
        <legend>Функции</legend>
        <UiCheckbox v-for="(label, key) in features" :key="key" v-model="form.features[key]" class="check-row">
          {{ label }}
        </UiCheckbox>
      </fieldset>
      <UiCheckbox v-model="form.is_trial" class="check-row">
        Пробный — выдаётся новым пользователям при регистрации (отметка снимется с прежнего пробного)
      </UiCheckbox>
      <UiCheckbox v-model="form.active" class="check-row">
        Включён — предлагается пользователям
      </UiCheckbox>
      <div v-field class="field">
        <label>Порядок в списке</label>
        <input v-model="form.position" type="number" step="1" inputmode="numeric" placeholder="0" />
      </div>
    </form>
    <template #footer>
      <button
        v-if="form?.id"
        class="btn btn--ghost"
        type="button"
        :disabled="busy"
        @click="remove"
      >
        Удалить
      </button>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="form = null">Отмена</button>
      <button class="btn btn--accent" type="submit" form="plan-form" :disabled="busy">
        {{ busy ? 'Сохраняем…' : 'Сохранить' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import Modal from '../../components/ui/Modal.vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { num, rubles } from '../../utils/format'

const emit = defineEmits(['changed'])

const loading = ref(true)
const items = ref([])
const upgrades = ref([])
const features = ref({})
const form = ref(null)
const busy = ref(false)
const error = ref('')
const savingCell = ref('')

// На пробный не переходят — он только для новых пользователей.
const targets = computed(() => items.value.filter((p) => !p.is_trial))

function missingFeatures(p) {
  return Object.entries(features.value)
    .filter(([key]) => p.features?.[key] === false)
    .map(([, label]) => label)
    .join(', ')
}

const cellKey = (from, to) => `${from}:${to}`

function discount(from, to) {
  const u = upgrades.value.find((x) => x.from_plan_id === from && x.to_plan_id === to)
  return u ? u.discount_pct : ''
}

async function load() {
  try {
    const data = await adminApi.plans()
    items.value = data.items || []
    upgrades.value = data.upgrades || []
    features.value = data.features || {}
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loading.value = false
  }
}

const blank = (v) => (v === null || v === undefined ? '' : String(v))
const numberOrNull = (v) => (String(v ?? '').trim() === '' ? null : Number(v))

function openNew() {
  error.value = ''
  form.value = {
    id: null,
    name: '',
    price_rub: '',
    days: 30,
    units_limit: '',
    projects_limit: '',
    discount_3_pct: '',
    discount_12_pct: '',
    features: Object.fromEntries(Object.keys(features.value).map((k) => [k, true])),
    is_trial: false,
    active: true,
    position: items.value.length,
  }
}

function openEdit(p) {
  error.value = ''
  form.value = {
    id: p.id,
    name: p.name,
    price_rub: blank(p.price_rub),
    days: p.days,
    units_limit: blank(p.units_limit),
    projects_limit: blank(p.projects_limit),
    discount_3_pct: p.discount_3_pct ? String(p.discount_3_pct) : '',
    discount_12_pct: p.discount_12_pct ? String(p.discount_12_pct) : '',
    features: Object.fromEntries(Object.keys(features.value).map((k) => [k, p.features?.[k] !== false])),
    is_trial: p.is_trial,
    active: p.active,
    position: p.position,
  }
}

async function save() {
  const f = form.value
  const payload = {
    name: f.name,
    price_rub: numberOrNull(f.price_rub) ?? 0,
    days: numberOrNull(f.days),
    units_limit: numberOrNull(f.units_limit),
    projects_limit: numberOrNull(f.projects_limit),
    discount_3_pct: numberOrNull(f.discount_3_pct) ?? 0,
    discount_12_pct: numberOrNull(f.discount_12_pct) ?? 0,
    features: { ...f.features },
    is_trial: f.is_trial,
    active: f.active,
    position: numberOrNull(f.position) ?? 0,
  }
  busy.value = true
  error.value = ''
  try {
    if (f.id) await adminApi.updatePlan(f.id, payload)
    else await adminApi.createPlan(payload)
    form.value = null
    pushToast('Тариф сохранён')
    await load()
    emit('changed')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

async function remove() {
  const f = form.value
  if (!(await askConfirm(`Удалить тариф «${f.name}»?`, { title: 'Удаление тарифа' }))) return
  busy.value = true
  error.value = ''
  try {
    await adminApi.deletePlan(f.id)
    form.value = null
    pushToast('Тариф удалён')
    await load()
    emit('changed')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

async function saveDiscount(from, to, input) {
  const key = cellKey(from.id, to.id)
  savingCell.value = key
  try {
    const res = await adminApi.setUpgrade(from.id, to.id, numberOrNull(input.value))
    upgrades.value = upgrades.value.filter((u) => !(u.from_plan_id === from.id && u.to_plan_id === to.id))
    if (res.discount_pct) upgrades.value.push(res)
    input.value = res.discount_pct || ''
    pushToast(res.discount_pct ? `Скидка ${num(res.discount_pct, 2)}% сохранена` : 'Скидка снята')
  } catch (err) {
    input.value = discount(from.id, to.id)
    pushToast(errorText(err), 'error')
  } finally {
    savingCell.value = ''
  }
}

onMounted(load)
</script>
