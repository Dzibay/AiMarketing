<template>
  <div class="site">
    <SiteHeader solid />
    <main class="cabinet">
      <div class="wrap">
        <div class="cabinet__head">
          <div>
            <h1>Клиенты</h1>
            <p>Карточка клиента: KPI, бюджет и его кабинеты Директа и счётчики Метрики.</p>
          </div>
        </div>
        <CabinetNav />

        <div v-if="loading" class="panel"><p class="empty">Загружаем клиентов…</p></div>

        <template v-else>
          <section class="panel">
            <h2>{{ editingId ? 'Редактирование клиента' : 'Новый клиент' }}</h2>
            <form class="filters" @submit.prevent="save">
              <div class="field">
                <label>Название</label>
                <input v-model="form.name" type="text" maxlength="120" placeholder="ООО «Ромашка»" required />
              </div>
              <div class="field">
                <label>Сайт</label>
                <input v-model="form.site" type="text" placeholder="romashka.ru" />
              </div>
              <div class="field">
                <label>KPI</label>
                <select v-model="form.kpi_type">
                  <option value="">Не задан</option>
                  <option value="cpa">CPA, ₽</option>
                  <option value="drr">ДРР, %</option>
                </select>
              </div>
              <div v-if="form.kpi_type" class="field">
                <label>{{ form.kpi_type === 'drr' ? 'Целевой ДРР, %' : 'Целевой CPA, ₽' }}</label>
                <input v-model.number="form.kpi_target" type="number" min="0" step="0.01" />
              </div>
              <div class="field">
                <label>Бюджет в месяц, ₽</label>
                <input v-model.number="form.monthly_budget_rub" type="number" min="0" step="1" />
              </div>
              <div class="field field--wide">
                <label>Заметки</label>
                <input v-model="form.notes" type="text" placeholder="Контакты, договорённости" />
              </div>
              <button class="btn btn--accent" type="submit" :disabled="busy">
                {{ editingId ? 'Сохранить' : 'Добавить' }}
              </button>
              <button v-if="editingId" class="btn btn--ghost" type="button" @click="resetForm">
                Отмена
              </button>
            </form>
          </section>

          <section class="panel">
            <h2>Список</h2>
            <p v-if="clients.length" class="hint">
              Новые кабинеты Директа при синке получают своего клиента автоматически; счётчики —
              если в подключении ровно один кабинет. Остальное привяжите ниже.
            </p>
            <div v-if="clients.length" class="list">
              <div
                v-for="c in clients"
                :key="c.id"
                class="list-row"
                :class="{ 'is-off': c.archived }"
              >
                <div>
                  <strong>{{ c.name }}</strong>
                  <small>
                    <template v-if="c.site">{{ c.site }} · </template>
                    {{ kpiLabel(c) }}
                    <template v-if="c.monthly_budget_rub != null">
                      · бюджет {{ rub(c.monthly_budget_rub) }}/мес
                    </template>
                    · Директ {{ c.direct_count }} · Метрика {{ c.metrika_count }}
                    <template v-if="c.archived"> · в архиве</template>
                  </small>
                  <small v-if="c.notes">{{ c.notes }}</small>
                </div>
                <div class="row-actions">
                  <button class="btn btn--ghost" type="button" :disabled="busy" @click="edit(c)">
                    Изменить
                  </button>
                  <button class="btn btn--ghost" type="button" :disabled="busy" @click="toggleArchive(c)">
                    {{ c.archived ? 'Вернуть' : 'В архив' }}
                  </button>
                  <button class="btn btn--ghost" type="button" :disabled="busy" @click="remove(c)">
                    Удалить
                  </button>
                </div>
              </div>
            </div>
            <p v-else class="empty">
              Клиентов пока нет. Подключите Яндекс клиента — карточка создастся при синке, или
              добавьте вручную.
            </p>
          </section>

          <section class="panel">
            <h2>Кабинеты Директа</h2>
            <div v-if="direct.length" class="list">
              <div v-for="a in direct" :key="a.id" class="list-row" :class="{ 'is-off': !a.enabled }">
                <div>
                  <strong>{{ a.name || a.client_login }}</strong>
                  <small>{{ a.client_login }}</small>
                </div>
                <div class="row-actions">
                  <select
                    :value="a.client_row_id || 0"
                    :disabled="busy"
                    @change="assign('direct', a, Number($event.target.value))"
                  >
                    <option :value="0">Без клиента</option>
                    <option v-for="c in assignable(a.client_row_id)" :key="c.id" :value="c.id">
                      {{ c.name }}
                    </option>
                  </select>
                </div>
              </div>
            </div>
            <p v-else class="empty">Кабинетов пока нет.</p>
          </section>

          <section class="panel">
            <h2>Счётчики Метрики</h2>
            <div v-if="metrika.length" class="list">
              <div v-for="m in metrika" :key="m.id" class="list-row" :class="{ 'is-off': !m.enabled }">
                <div>
                  <strong>{{ m.name || `Счётчик ${m.counter_id}` }}</strong>
                  <small>{{ m.site || `ID ${m.counter_id}` }}</small>
                </div>
                <div class="row-actions">
                  <select
                    :value="m.client_row_id || 0"
                    :disabled="busy"
                    @change="assign('metrika', m, Number($event.target.value))"
                  >
                    <option :value="0">Без клиента</option>
                    <option v-for="c in assignable(m.client_row_id)" :key="c.id" :value="c.id">
                      {{ c.name }}
                    </option>
                  </select>
                </div>
              </div>
            </div>
            <p v-else class="empty">Счётчиков пока нет.</p>
          </section>
        </template>
      </div>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import CabinetNav from '../components/CabinetNav.vue'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { errorText, webApi } from '../api/web'
import { getSession } from '../composables/useSession'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'

useHead('Клиенты — AiMarketing', true)

const router = useRouter()
const loading = ref(true)
const busy = ref(false)
const clients = ref([])
const direct = ref([])
const metrika = ref([])
const editingId = ref(null)

const emptyForm = () => ({
  name: '',
  site: '',
  notes: '',
  kpi_type: '',
  kpi_target: null,
  monthly_budget_rub: null,
})
const form = reactive(emptyForm())

function rub(value) {
  return `${Number(value).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₽`
}

function kpiLabel(c) {
  if (c.kpi_type === 'cpa') return c.kpi_target != null ? `CPA ≤ ${rub(c.kpi_target)}` : 'CPA'
  if (c.kpi_type === 'drr') return c.kpi_target != null ? `ДРР ≤ ${c.kpi_target}%` : 'ДРР'
  return 'KPI не задан'
}

// Архивных клиентов в выпадающем списке не предлагаем, но текущую привязку показываем.
function assignable(currentId) {
  return clients.value.filter((c) => !c.archived || c.id === currentId)
}

async function load() {
  if (!getSession()) {
    router.replace('/login')
    return
  }
  try {
    const me = await webApi.me()
    clients.value = me.clients || []
    direct.value = me.direct || []
    metrika.value = me.metrika || []
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loading.value = false
  }
}

function resetForm() {
  editingId.value = null
  Object.assign(form, emptyForm())
}

function edit(c) {
  editingId.value = c.id
  Object.assign(form, {
    name: c.name,
    site: c.site,
    notes: c.notes,
    kpi_type: c.kpi_type,
    kpi_target: c.kpi_target,
    monthly_budget_rub: c.monthly_budget_rub,
  })
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function payload() {
  const num = (v) => (v === '' || v === null || v === undefined ? null : Number(v))
  return {
    name: form.name.trim(),
    site: form.site.trim(),
    notes: form.notes.trim(),
    kpi_type: form.kpi_type,
    kpi_target: form.kpi_type ? num(form.kpi_target) : null,
    monthly_budget_rub: num(form.monthly_budget_rub),
  }
}

async function save() {
  busy.value = true
  try {
    if (editingId.value) {
      await webApi.patchClient(editingId.value, payload())
      pushToast('Клиент сохранён')
    } else {
      await webApi.createClient(payload())
      pushToast('Клиент добавлен')
    }
    resetForm()
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function toggleArchive(c) {
  busy.value = true
  try {
    await webApi.patchClient(c.id, { archived: !c.archived })
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function remove(c) {
  const linked = c.direct_count + c.metrika_count
  const note = linked ? ` Кабинеты и счётчики (${linked}) останутся, но без клиента.` : ''
  if (!window.confirm(`Удалить клиента «${c.name}»?${note}`)) return
  busy.value = true
  try {
    await webApi.deleteClient(c.id)
    if (editingId.value === c.id) resetForm()
    pushToast('Клиент удалён')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function assign(kind, item, clientId) {
  busy.value = true
  try {
    if (kind === 'direct') await webApi.assignDirectClient(item.id, clientId)
    else await webApi.assignMetrikaClient(item.id, clientId)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>
