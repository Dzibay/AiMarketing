<template>
  <section class="panel" id="site-contacts">
    <div class="panel__head"><h2>Контакты на сайте</h2></div>
    <p class="hint">
      Подвал, «Контакты» и формы tolk-ads.ru берут их отсюда — пересобирать сайт не нужно, новые данные появятся в браузере
      сразу и в разметке после следующей сборки. Пустое поле на сайте не показывается.
    </p>
    <form class="form-grid" @submit.prevent="save">
      <div v-field class="field">
        <label>Почта поддержки</label>
        <input v-model.trim="form.email" type="email" placeholder="support@tolk-ads.ru" autocomplete="off" />
      </div>
      <p class="field-hint">Она же — в оферте, политике и согласии на обработку данных.</p>
      <div v-field class="field">
        <label>Telegram</label>
        <input v-model.trim="form.telegram" type="text" placeholder="tolk_support" autocomplete="off" />
      </div>
      <div v-field class="field">
        <label>Реквизиты одной строкой</label>
        <textarea v-model="form.legal" rows="2" maxlength="400" placeholder="ИП Иванов Иван Иванович, ИНН 000000000000, ОГРНИП 000000000000000" />
      </div>
      <p class="field-hint">Для ЮKassa: ФИО или название, ИНН, ОГРН(ИП). Показываются в подвале и на странице «Контакты».</p>

      <h3 class="form-sub">SmartCaptcha</h3>
      <p class="field-hint">
        Невидимая капча Яндекс Облака на живой проверке сайта, листе ожидания и ИИ-генераторах. Без ключа сервера капча
        выключена — работают только суточные лимиты.
        <span class="chip" :class="data.captcha.enabled ? 'chip--ok' : ''">{{ data.captcha.enabled ? 'включена' : 'выключена' }}</span>
      </p>
      <div v-field class="field">
        <label>Ключ клиента</label>
        <input v-model.trim="form.captcha_client_key" type="text" autocomplete="off" />
      </div>
      <div v-field class="field">
        <label>Ключ сервера</label>
        <input
          v-model.trim="form.captcha_server_key"
          type="password"
          :placeholder="data.captcha.server_key_set ? 'задан — оставьте пустым, чтобы не менять' : ''"
          autocomplete="new-password"
        />
      </div>
      <UiCheckbox v-if="data.captcha.server_key_set" v-model="form.captcha_clear" class="check-row">
        Стереть ключ сервера
      </UiCheckbox>

      <h3 class="form-sub">ИИ-генераторы</h3>
      <p class="field-hint">
        Кнопка «Подобрать с ИИ» в генераторах минус-слов и объявлений. Модель — та же, что у проверки сайта (Настройки → «Проверка
        сайта»), расход — в «Расходах на ИИ» без пользователя. Сегодня: {{ num(data.tools.stats.today) }} · за 7 дней:
        {{ num(data.tools.stats.week) }}.
      </p>
      <UiCheckbox v-model="form.tools_enabled" class="check-row">
        Включены
      </UiCheckbox>
      <div v-field class="field">
        <label>Запусков с одного IP в сутки</label>
        <input v-model="form.tools_ip_daily" type="number" min="1" max="200" step="1" inputmode="numeric" />
      </div>
      <div v-field class="field">
        <label>Всего запусков в сутки</label>
        <input v-model="form.tools_daily" type="number" min="1" step="1" inputmode="numeric" />
      </div>
      <div class="form-actions">
        <button class="btn btn--accent" type="submit" :disabled="busy">Сохранить</button>
      </div>
    </form>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import { formatDateTime, num } from '../../utils/format'

const data = ref({ contacts: {}, captcha: {}, tools: { stats: {} } })
const form = reactive({
  email: '',
  telegram: '',
  legal: '',
  captcha_client_key: '',
  captcha_server_key: '',
  captcha_clear: false,
  tools_enabled: true,
  tools_ip_daily: 10,
  tools_daily: 500,
})
const busy = ref(false)

function fill(value) {
  data.value = { ...value, tools: { ...value.tools, stats: value.tools?.stats || {} } }
  form.email = value.contacts.email || ''
  form.telegram = value.contacts.telegram || ''
  form.legal = value.contacts.legal || ''
  form.captcha_client_key = value.captcha.client_key || ''
  form.captcha_server_key = ''
  form.captcha_clear = false
  form.tools_enabled = !!value.tools.enabled
  form.tools_ip_daily = value.tools.ip_daily
  form.tools_daily = value.tools.daily
}

async function save() {
  busy.value = true
  try {
    fill(
      await adminApi.saveSite({
        email: form.email,
        telegram: form.telegram,
        legal: form.legal,
        captcha_client_key: form.captcha_client_key,
        captcha_server_key: form.captcha_clear ? '' : form.captcha_server_key || null,
        tools_enabled: form.tools_enabled,
        tools_ip_daily: Number(form.tools_ip_daily),
        tools_daily: Number(form.tools_daily),
      }),
    )
    pushToast('Настройки сайта сохранены')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try {
    fill(await adminApi.site())
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
})
</script>

<style scoped>
.form-sub {
  margin: 12px 0 0;
  font-size: 15px;
}
</style>
