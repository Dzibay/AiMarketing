<template>
  <section class="panel" id="site-check">
    <div class="panel__head">
      <h2>Проверка сайта на лендинге</h2>
      <span class="chip" :class="data.enabled && data.effective_model ? 'chip--ok' : 'chip--warning'">
        {{ !data.enabled ? 'выключена' : data.effective_model ? 'включена' : 'нет модели' }}
      </span>
    </div>
    <p class="hint">
      Посетитель лендинга вставляет адрес сайта — ИИ описывает нишу и предлагает заголовки и минус-слова. Без регистрации,
      расход пишется в «Расход ИИ» без пользователя.
    </p>
    <form class="form-grid" @submit.prevent="save">
      <label class="check-row">
        <input v-model="form.enabled" type="checkbox" />
        <span>Включена — форма в первом экране лендинга проверяет сайт по-настоящему</span>
      </label>
      <div v-field class="field">
        <label>Модель</label>
        <input v-model.trim="form.model" type="text" :placeholder="data.class_model || 'модель класса'" autocomplete="off" />
      </div>
      <p class="field-hint">
        Тот же адрес API и ключ, что в разделе «ИИ». Пусто — модель класса
        <template v-if="data.class_name">«{{ data.class_name }}»</template> из «Фоновые задачи»; цены и себестоимость — этого
        класса.
        <template v-if="data.effective_model">Сейчас отвечает: <b>{{ data.effective_model }}</b>.</template>
      </p>
      <div v-field class="field">
        <label>Проверок с одного IP в сутки</label>
        <input v-model="form.ip_daily" type="number" min="1" max="100" step="1" inputmode="numeric" />
      </div>
      <div v-field class="field">
        <label>Всего проверок в сутки</label>
        <input v-model="form.daily" type="number" min="1" step="1" inputmode="numeric" />
      </div>
      <div v-field class="field">
        <label>Кэш по домену, дней</label>
        <input v-model="form.cache_days" type="number" min="0" max="90" step="1" inputmode="numeric" />
      </div>
      <p class="field-hint">Повторная проверка того же сайта в пределах кэша берётся готовой и в лимиты не входит.</p>
      <div class="form-actions">
        <button class="btn btn--accent" type="submit" :disabled="busy">Сохранить</button>
        <button class="btn btn--ghost" type="button" :disabled="busy || !data.effective_model" @click="test">
          {{ testing ? 'Проверяем…' : 'Проверить модель' }}
        </button>
      </div>
    </form>
    <p v-if="result" class="sync-status" :class="result.error ? 'is-error' : 'is-ok'">
      <template v-if="result.error">{{ result.error }}</template>
      <template v-else>
        <b>{{ result.model }}</b> ответила «{{ result.reply }}»<template v-if="result.answered_by && result.answered_by !== result.model">
          (провайдер: {{ result.answered_by }})</template>
      </template>
    </p>
    <p class="sync-status">
      Сегодня: {{ num(data.stats.today) }}<template v-if="data.stats.today_errors"> (ошибок: {{ num(data.stats.today_errors) }})</template>
      · за 7 дней: {{ num(data.stats.week) }} проверок, {{ num(data.stats.week_domains) }} сайтов
    </p>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import { num } from '../../utils/format'

const data = ref({ enabled: true, model: '', effective_model: '', class_name: '', stats: {} })
const form = reactive({ enabled: true, model: '', ip_daily: 3, daily: 300, cache_days: 7 })
const busy = ref(false)
const testing = ref(false)
const result = ref(null)


function fill(value) {
  data.value = { ...value, stats: value.stats || {} }
  form.enabled = !!value.enabled
  form.model = value.model || ''
  form.ip_daily = value.ip_daily
  form.daily = value.daily
  form.cache_days = value.cache_days
}

async function save() {
  busy.value = true
  result.value = null
  try {
    fill(
      await adminApi.saveSiteCheck({
        enabled: form.enabled,
        model: form.model,
        ip_daily: Number(form.ip_daily),
        daily: Number(form.daily),
        cache_days: Number(form.cache_days),
      }),
    )
    pushToast('Настройки проверки сайта сохранены')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function test() {
  busy.value = true
  testing.value = true
  try {
    const res = await adminApi.testSiteCheck()
    result.value = { ...res, reply: String(res.reply || '').slice(0, 120) }
  } catch (err) {
    result.value = { error: errorText(err) }
  } finally {
    busy.value = false
    testing.value = false
  }
}

onMounted(async () => {
  try {
    fill(await adminApi.siteCheck())
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
})
</script>
