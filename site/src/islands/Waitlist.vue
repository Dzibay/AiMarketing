<script setup>
// Лист ожидания на странице интеграции «скоро»: почта + согласие → POST /api/site/waitlist, цель waitlist_submit.
import { ref } from 'vue'
import { goal } from '../lib/goal.js'
import { captchaToken } from '../lib/public.js'

const props = defineProps({ channel: { type: String, required: true }, title: { type: String, required: true } })

const email = ref('')
const consent = ref(false)
const busy = ref(false)
const done = ref('')
const error = ref('')

async function submit() {
  error.value = ''
  if (!consent.value) {
    error.value = 'Отметьте согласие на обработку почты'
    return
  }
  busy.value = true
  try {
    const captcha = await captchaToken()
    const res = await fetch('/api/site/waitlist', {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel: props.channel, email: email.value, consent: consent.value, captcha }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      error.value = data?.detail?.message || 'Не получилось записать — попробуйте ещё раз'
      return
    }
    done.value = data.message || 'Готово'
    goal('waitlist_submit', { channel: props.channel })
  } catch {
    error.value = 'Нет связи с сервером — попробуйте ещё раз'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <p v-if="done" class="wl__done" role="status">{{ done }}</p>
  <form v-else class="wl" @submit.prevent="submit">
    <label class="sr-only" :for="`wl-${channel}`">Почта</label>
    <div class="wl__row">
      <input
        :id="`wl-${channel}`"
        v-model.trim="email"
        type="email"
        required
        maxlength="200"
        autocomplete="email"
        placeholder="почта@пример.рф"
      />
      <button class="btn btn--lime" type="submit" :disabled="busy">{{ busy ? 'Отправляем…' : 'Сообщить мне' }}</button>
    </div>
    <label class="wl__consent">
      <input v-model="consent" type="checkbox" />
      <span>
        Соглашаюсь на обработку почты, чтобы получить письмо о запуске «{{ title }}», — по
        <a href="/consent" target="_blank" rel="noopener">согласию</a> и
        <a href="/privacy" target="_blank" rel="noopener">политике</a>.
      </span>
    </label>
    <p v-if="error" class="wl__error" role="alert">{{ error }}</p>
  </form>
</template>

<style scoped>
.wl {
  display: grid;
  gap: 12px;
  margin-top: 4px;
}
.wl__row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.wl__row input {
  flex: 1 1 220px;
  min-width: 0;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface, #fff);
  color: var(--ink);
  font: inherit;
}
.wl__row input:focus-visible {
  outline: 2px solid var(--lime);
  outline-offset: 1px;
}
.wl__consent {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  font-size: 13px;
  color: var(--muted);
}
.wl__consent input {
  margin-top: 3px;
  accent-color: var(--ink);
}
.wl__error {
  color: var(--danger, #c0392b);
  font-size: 14px;
  margin: 0;
}
.wl__done {
  font-weight: 700;
}
</style>
