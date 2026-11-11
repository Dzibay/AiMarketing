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
      <label v-if="data.captcha.server_key_set" class="check-row">
        <input v-model="form.captcha_clear" type="checkbox" />
        <span>Стереть ключ сервера</span>
      </label>

      <h3 class="form-sub">ИИ-генераторы</h3>
      <p class="field-hint">
        Кнопка «Подобрать с ИИ» в генераторах минус-слов и объявлений. Модель — та же, что у проверки сайта (раздел «ИИ и
        сервисы»), расход — в «Расход» без пользователя. Сегодня: {{ num(data.tools.stats.today) }} · за 7 дней:
        {{ num(data.tools.stats.week) }}.
      </p>
      <label class="check-row">
        <input v-model="form.tools_enabled" type="checkbox" />
        <span>Включены</span>
      </label>
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

  <section class="panel" id="site-waitlist">
    <div class="panel__head">
      <h2>Лист ожидания</h2>
      <span v-for="c in waitlist.channels" :key="c.id" class="chip">{{ c.label }}: {{ num(c.count) }}</span>
    </div>
    <p class="hint">Почты со страниц интеграций «скоро». Напишите им, когда раздел запустится.</p>
    <p v-if="!waitlist.items.length" class="empty">Заявок пока нет.</p>
    <div v-else class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>Когда</th>
            <th>Раздел</th>
            <th>Почта</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="w in waitlist.items" :key="w.id">
            <td>{{ formatDateTime(w.created_at) }}</td>
            <td>{{ w.label }}</td>
            <td><a :href="`mailto:${w.email}`">{{ w.email }}</a></td>
            <td>
              <button class="btn btn--ghost btn--xs btn--danger" type="button" :disabled="rowBusy === w.id" @click="remove(w)">
                Удалить
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
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
const waitlist = ref({ channels: [], items: [] })
const busy = ref(false)
const rowBusy = ref(null)

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

async function remove(w) {
  if (!(await askConfirm(`Удалить ${w.email} из листа ожидания «${w.label}»?`, { danger: true }))) return
  rowBusy.value = w.id
  try {
    waitlist.value = await adminApi.deleteWaitlist(w.id)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    rowBusy.value = null
  }
}

onMounted(async () => {
  try {
    const [settings, list] = await Promise.all([adminApi.site(), adminApi.waitlist()])
    fill(settings)
    waitlist.value = list
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
