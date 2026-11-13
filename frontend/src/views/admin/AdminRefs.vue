<template>
  <section class="panel">
    <div class="panel__head">
      <h2>Реф-ссылки</h2>
    </div>
    <p class="hint">
      Ссылка вида <code>/?ref=код</code> — для блогера, рассылки или рекламы. Код запоминается в браузере при первом
      заходе на 90 дней и записывается пользователю при регистрации (почтой или через Яндекс); последующие ссылки его не
      перебивают. Выручка — оплаты этих пользователей без тестовых и за вычетом возвратов.
    </p>
    <form class="filters ref-form" @submit.prevent="create">
      <div v-field class="field">
        <label>Код</label>
        <input
          v-model.trim="draft.code"
          type="text"
          maxlength="40"
          required
          autocomplete="off"
          placeholder="blogger-anna"
          @input="draft.code = draft.code.toLowerCase()"
        />
      </div>
      <div v-field class="field field--wide">
        <label>Название</label>
        <input v-model="draft.name" type="text" maxlength="120" placeholder="Кто или где размещает ссылку" />
      </div>
      <button class="btn btn--accent" type="submit" :disabled="busy">Создать ссылку</button>
    </form>
    <small class="field-hint">Код — латинские буквы, цифры, «-» и «_», от 2 до 40 символов.</small>
  </section>

  <section class="panel">
    <div class="panel__head">
      <h2>Ссылки для друзей</h2>
    </div>
    <p class="hint">
      У каждого пользователя на странице «Тариф» своя ссылка. Когда приглашённый по ней впервые оплатит тариф (не
      тестовым платежом), оба получают дни к подписке. 0 — без бонуса. Подписка без срока бонус не получает.
    </p>
    <form class="filters ref-form" @submit.prevent="saveBonus">
      <div v-field class="field">
        <label>Пригласившему, дней</label>
        <input v-model="bonus.referrer_days" type="number" min="0" max="365" step="1" inputmode="numeric" />
      </div>
      <div v-field class="field">
        <label>Приглашённому, дней</label>
        <input v-model="bonus.invitee_days" type="number" min="0" max="365" step="1" inputmode="numeric" />
      </div>
      <button class="btn btn--accent" type="submit" :disabled="bonusBusy">Сохранить</button>
    </form>
    <p v-if="personal" class="hint">
      Личных ссылок: {{ num(personal.links) }} · пришло по ним: {{ num(personal.registrations) }} · бонусов выдано:
      {{ num(personal.bonuses) }} ({{ num(personal.bonus_days) }} {{ plural(personal.bonus_days, 'день', 'дня', 'дней') }}
      на двоих)
    </p>
  </section>

  <section class="panel">
    <div class="panel__head">
      <h2>Статистика</h2>
      <button class="btn btn--ghost btn--xs" type="button" :disabled="loading" @click="load">Обновить</button>
    </div>
    <p v-if="loading && !items.length" class="empty">Загружаем…</p>
    <p v-else-if="!items.length" class="empty">Ссылок пока нет.</p>
    <div v-else v-scroll-x class="table-scroll">
      <table class="table admin-refs">
        <thead>
          <tr>
            <th>Ссылка</th>
            <th class="num">Переходы</th>
            <th class="num">Регистрации</th>
            <th class="num">Подтвердили почту</th>
            <th class="num">Оплатили</th>
            <th class="num">Выручка</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in items" :key="r.id" :class="{ 'is-muted': !r.active }">
            <td>
              <strong>{{ r.name || r.code }}</strong>
              <span v-if="!r.active" class="chip chip--info">выключена</span>
              <small class="cell-note">{{ r.url }}</small>
            </td>
            <td class="num">{{ num(r.visits) }}</td>
            <td class="num">
              {{ num(r.registrations) }}
              <small v-if="r.visits" class="cell-note">{{ share(r.registrations, r.visits) }} переходов</small>
            </td>
            <td class="num">{{ num(r.verified) }}</td>
            <td class="num">
              {{ num(r.payers) }}
              <small v-if="r.payments" class="cell-note">
                {{ num(r.payments) }} {{ plural(r.payments, 'платёж', 'платежа', 'платежей') }}
              </small>
            </td>
            <td class="num">{{ rubles(r.revenue_rub) }}</td>
            <td class="row-actions">
              <button class="btn btn--ghost btn--xs" type="button" @click="copy(r.url)">Копировать</button>
              <button class="btn btn--ghost btn--xs" type="button" :disabled="rowBusy === r.id" @click="rename(r)">
                Название
              </button>
              <button class="btn btn--ghost btn--xs" type="button" :disabled="rowBusy === r.id" @click="toggle(r)">
                {{ r.active ? 'Выключить' : 'Включить' }}
              </button>
              <button class="btn btn--ghost btn--xs" type="button" :disabled="rowBusy === r.id" @click="remove(r)">
                Удалить
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="items.length" class="hint">
      Выключенная ссылка не считает переходы и не записывается новым пользователям; уже пришедшие остаются в статистике.
    </p>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { askConfirm, askText } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { num, plural, rubles } from '../../utils/format'

const items = ref([])
const loading = ref(true)
const busy = ref(false)
const rowBusy = ref(null)
const draft = reactive({ code: '', name: '' })
const bonus = reactive({ referrer_days: 0, invitee_days: 0 })
const bonusBusy = ref(false)
const personal = ref(null)

function share(part, total) {
  return `${Math.round((part / total) * 1000) / 10}%`
}

async function load() {
  loading.value = true
  try {
    const data = await adminApi.refs()
    items.value = data.items || []
    Object.assign(bonus, data.bonus || {})
    personal.value = data.personal || null
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loading.value = false
  }
}

async function create() {
  busy.value = true
  try {
    const row = await adminApi.createRef({ code: draft.code, name: draft.name, active: true })
    items.value = [row, ...items.value]
    draft.code = ''
    draft.name = ''
    pushToast('Ссылка создана')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function saveBonus() {
  bonusBusy.value = true
  try {
    const res = await adminApi.saveRefBonus({
      referrer_days: Number(bonus.referrer_days) || 0,
      invitee_days: Number(bonus.invitee_days) || 0,
    })
    Object.assign(bonus, res)
    pushToast(res.referrer_days || res.invitee_days ? 'Бонус сохранён' : 'Бонус выключен')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    bonusBusy.value = false
  }
}

async function update(r, patch) {
  rowBusy.value = r.id
  try {
    const row = await adminApi.updateRef(r.id, { code: r.code, name: r.name, active: r.active, ...patch })
    items.value = items.value.map((x) => (x.id === r.id ? row : x))
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    rowBusy.value = null
  }
}

async function rename(r) {
  const name = await askText('Название', { title: `Ссылка ${r.code}`, value: r.name, placeholder: 'Кто или где размещает ссылку' })
  if (name === null) return
  update(r, { name })
}

function toggle(r) {
  update(r, { active: !r.active })
}

async function remove(r) {
  const who = r.registrations ? ` Пришедшие по ней пользователи (${num(r.registrations)}) останутся, но без источника.` : ''
  if (!(await askConfirm(`Удалить ссылку ${r.code} и её статистику?${who}`, { danger: true }))) return
  rowBusy.value = r.id
  try {
    await adminApi.deleteRef(r.id)
    items.value = items.value.filter((x) => x.id !== r.id)
    pushToast('Ссылка удалена')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    rowBusy.value = null
  }
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    pushToast('Ссылка скопирована')
  } catch {
    pushToast('Не удалось скопировать — выделите ссылку вручную', 'error')
  }
}

onMounted(load)
</script>
