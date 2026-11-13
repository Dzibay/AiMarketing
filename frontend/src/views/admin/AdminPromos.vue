<template>
  <section class="panel">
    <div class="panel__head">
      <h2>Промокоды</h2>
      <button class="btn btn--accent btn--xs" type="button" @click="openNew">Добавить промокод</button>
    </div>
    <p class="hint">
      Промокод вводят при регистрации или в кабинете на странице «Тариф»; один пользователь активирует код один раз.
      Единицы добавляются к лимиту текущего периода и сгорают вместе с ним. Дни продлевают текущую подписку.
      Тариф на N дней подключается только тем, у кого пробный тариф или подписка закончилась, — оплаченный тариф
      промокод не перебивает. Ссылка вида <code>/register?promo=КОД</code> подставит код в форму регистрации.
    </p>
    <p v-if="loading" class="empty">Загружаем…</p>
    <p v-else-if="!items.length" class="empty">Промокодов пока нет.</p>
    <div v-else class="table-scroll">
      <table class="table admin-promos">
        <thead>
          <tr>
            <th>Код</th>
            <th>Даёт</th>
            <th class="num">Активаций</th>
            <th>Действует до</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in items" :key="p.id" :class="{ 'is-muted': !p.active || expired(p) }">
            <td>
              <strong class="promo-code">{{ p.code }}</strong>
              <span v-if="!p.active" class="chip chip--info">выключен</span>
              <span v-else-if="expired(p)" class="chip chip--warning">истёк</span>
              <span v-else-if="usedUp(p)" class="chip chip--warning">исчерпан</span>
              <small v-if="p.note" class="cell-note">{{ p.note }}</small>
            </td>
            <td>{{ gives(p) }}</td>
            <td class="num">{{ num(p.uses) }}<span class="cell-note"> / {{ p.max_uses ? num(p.max_uses) : '∞' }}</span></td>
            <td>{{ p.valid_until ? formatDay(p.valid_until) : 'бессрочно' }}</td>
            <td class="row-actions">
              <button class="btn btn--ghost btn--xs" type="button" @click="copy(p.code)">Копировать</button>
              <button class="btn btn--ghost btn--xs" type="button" @click="openEdit(p)">Изменить</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <Modal :open="!!form" :title="form?.id ? `Промокод ${form.code || '…'}` : 'Новый промокод'" :locked="busy" @close="form = null">
    <form v-if="form" id="promo-form" class="form-grid" @submit.prevent="save">
      <p v-if="error" class="form-error">{{ error }}</p>
      <div v-field class="field">
        <label>Код</label>
        <input
          v-model="form.code"
          type="text"
          maxlength="32"
          required
          autocomplete="off"
          placeholder="START30"
          class="promo-code"
          @input="form.code = form.code.toUpperCase()"
        />
        <small class="field-hint">3–32 символа: латинские буквы, цифры, «-» и «_». Регистр не важен.</small>
      </div>
      <div v-field class="field">
        <label>Что даёт</label>
        <UiSelect v-model="form.kind" :options="kindOptions" />
      </div>
      <div v-if="form.kind === 'units'" v-field class="field">
        <label>Единиц к лимиту</label>
        <input v-model="form.units" type="number" min="0" step="any" inputmode="decimal" required />
        <small class="field-hint">Добавляются к лимиту текущего периода и сгорают в его конце.</small>
      </div>
      <div v-if="form.kind === 'plan'" v-field class="field">
        <label>Тариф</label>
        <UiSelect v-model="form.plan_id" :options="planOptions" placeholder="Выберите тариф" required />
      </div>
      <div v-if="form.kind !== 'units'" v-field class="field">
        <label>Дней</label>
        <input v-model="form.days" type="number" min="1" max="3660" step="1" inputmode="numeric" required />
        <small class="field-hint">
          {{ form.kind === 'plan' ? 'Срок тарифа с момента активации.' : 'Добавляются к концу текущей подписки.' }}
        </small>
      </div>
      <div v-field class="field">
        <label>Активаций всего</label>
        <input v-model="form.max_uses" type="number" min="1" step="1" inputmode="numeric" placeholder="без ограничений" />
      </div>
      <div v-field class="field">
        <label>Действует до</label>
        <UiDatePicker v-model="form.valid_until" clearable clear-label="Бессрочно" />
        <small class="field-hint">Включительно, по Москве.</small>
      </div>
      <div v-field class="field field--wide">
        <label>Заметка</label>
        <input v-model="form.note" type="text" maxlength="200" placeholder="Для кого и зачем — видно только в админке" />
      </div>
      <UiCheckbox v-model="form.active" class="check-row">
        Включён — код можно активировать
      </UiCheckbox>
      <p v-if="form.id && form.uses" class="field-hint">
        Код уже активировали {{ num(form.uses) }} {{ plural(form.uses, 'раз', 'раза', 'раз') }}. Новые условия коснутся
        только следующих активаций.
      </p>
    </form>
    <template #footer>
      <button v-if="form?.id" class="btn btn--ghost" type="button" :disabled="busy" @click="remove">
        Удалить
      </button>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="form = null">Отмена</button>
      <button class="btn btn--accent" type="submit" form="promo-form" :disabled="busy">
        {{ busy ? 'Сохраняем…' : 'Сохранить' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import Modal from '../../components/ui/Modal.vue'
import UiDatePicker from '../../components/ui/UiDatePicker.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { formatDay, isoDay, num, plural } from '../../utils/format'

const items = ref([])
const kinds = ref({ units: 'Единицы к лимиту', days: 'Дни к подписке', plan: 'Тариф на N дней' })
const plans = ref([])
const kindOptions = computed(() => Object.entries(kinds.value).map(([value, label]) => ({ value, label })))
const planOptions = computed(() =>
  plans.value.map((pl) => ({ value: pl.id, label: `${pl.name}${pl.active ? '' : ' (выключен)'}` })),
)
const loading = ref(true)
const form = ref(null)
const busy = ref(false)
const error = ref('')

function expired(p) {
  return !!p.valid_until && p.valid_until < isoDay(new Date())
}

function usedUp(p) {
  return !!p.max_uses && p.uses >= p.max_uses
}

function gives(p) {
  if (p.kind === 'units') return `+${num(p.units, 2)} ед. к лимиту периода`
  if (p.kind === 'days') return `+${num(p.days)} дн. к подписке`
  return `«${p.plan_name || 'тариф удалён'}» на ${num(p.days)} дн.`
}

function openNew() {
  error.value = ''
  form.value = {
    id: null,
    code: '',
    kind: 'days',
    units: '',
    days: 14,
    plan_id: null,
    max_uses: '',
    valid_until: '',
    note: '',
    active: true,
    uses: 0,
  }
}

function openEdit(p) {
  error.value = ''
  form.value = {
    ...p,
    units: p.units ?? '',
    days: p.days ?? '',
    max_uses: p.max_uses ?? '',
    valid_until: p.valid_until || '',
  }
}

function blank(value) {
  return value === '' || value === null || value === undefined ? null : value
}

async function save() {
  const f = form.value
  busy.value = true
  error.value = ''
  const payload = {
    code: f.code,
    kind: f.kind,
    units: f.kind === 'units' ? blank(f.units) : null,
    days: f.kind === 'units' ? null : blank(f.days),
    plan_id: f.kind === 'plan' ? f.plan_id : null,
    max_uses: blank(f.max_uses),
    valid_until: blank(f.valid_until),
    active: f.active,
    note: f.note,
  }
  try {
    if (f.id) await adminApi.updatePromo(f.id, payload)
    else await adminApi.createPromo(payload)
    form.value = null
    pushToast('Промокод сохранён')
    load()
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

async function remove() {
  const f = form.value
  const note = f.uses ? ' Активации останутся в истории пользователей.' : ''
  if (!(await askConfirm(`Удалить промокод ${f.code}?${note}`, { danger: true }))) return
  busy.value = true
  try {
    await adminApi.deletePromo(f.id)
    form.value = null
    pushToast('Промокод удалён')
    load()
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    pushToast('Скопировано')
  } catch {
    pushToast('Не удалось скопировать', 'error')
  }
}

async function load() {
  try {
    const data = await adminApi.promos()
    items.value = data.items || []
    if (data.kinds) kinds.value = data.kinds
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  load()
  try {
    plans.value = ((await adminApi.plans()).items || []).filter((p) => !p.is_trial)
  } catch {
    plans.value = []
  }
})
</script>
