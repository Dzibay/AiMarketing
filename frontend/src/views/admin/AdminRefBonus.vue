<template>
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
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import { num, plural } from '../../utils/format'

const bonus = reactive({ referrer_days: 0, invitee_days: 0 })
const bonusBusy = ref(false)
const personal = ref(null)

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

onMounted(async () => {
  try {
    const data = await adminApi.refs()
    Object.assign(bonus, data.bonus || {})
    personal.value = data.personal || null
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
})
</script>
