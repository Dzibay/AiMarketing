<template>
  <div class="site">
    <SiteHeader solid />
    <main class="cabinet">
      <div class="wrap">
        <div class="cabinet__head">
          <div>
            <h1>Кампании</h1>
            <p>Создание, редактирование, пауза, архив и удаление в Директе.</p>
          </div>
        </div>
        <CabinetNav />

        <section class="panel">
          <h2>Новая кампания</h2>
          <p class="hint">Текстово-графическая на поиске (HIGHEST_POSITION). Объявления добавляются уже в Директе.</p>
          <div class="filters">
            <div class="field">
              <label>Аккаунт</label>
              <select v-model="createForm.account_id">
                <option value="">Выберите</option>
                <option v-for="a in accounts" :key="a.id" :value="String(a.id)">
                  {{ a.name || a.client_login }}
                </option>
              </select>
            </div>
            <div class="field">
              <label>Название</label>
              <input v-model="createForm.name" type="text" placeholder="Кампания API" />
            </div>
            <div class="field">
              <label>Бюджет ₽/день</label>
              <input v-model.number="createForm.daily_budget_rub" type="number" min="1" step="1" />
            </div>
            <div class="field">
              <label>Старт</label>
              <input v-model="createForm.start_date" type="date" />
            </div>
            <button class="btn btn--accent" type="button" :disabled="creating" @click="createCampaign">
              {{ creating ? 'Создаём…' : 'Создать' }}
            </button>
          </div>
        </section>

        <div class="filters">
          <div class="field">
            <label>Аккаунт</label>
            <select v-model="accountId" @change="onFilter">
              <option value="">Все</option>
              <option v-for="a in accounts" :key="a.id" :value="String(a.id)">
                {{ a.name || a.client_login }}
              </option>
            </select>
          </div>
          <div class="field">
            <label>Статус</label>
            <select v-model="state" @change="onFilter">
              <option value="">Все</option>
              <option value="ON">ON</option>
              <option value="SUSPENDED">SUSPENDED</option>
              <option value="OFF">OFF</option>
              <option value="ARCHIVED">ARCHIVED</option>
            </select>
          </div>
          <div class="field">
            <label>Поиск</label>
            <input v-model="search" type="search" placeholder="Имя или ID" @keyup.enter="onFilter" />
          </div>
          <div class="field">
            <label>На странице</label>
            <select v-model.number="pageSize" @change="onFilter">
              <option :value="25">25</option>
              <option :value="50">50</option>
              <option :value="100">100</option>
            </select>
          </div>
          <button class="btn btn--ghost" type="button" @click="onFilter">Найти</button>
          <button class="btn btn--accent" type="button" :disabled="syncing" @click="syncAll">
            Синк кампаний
          </button>
        </div>

        <div v-if="selected.size" class="bulk-bar">
          <span>Выбрано: {{ selected.size }}</span>
          <button class="btn btn--ghost" type="button" @click="runAction('pause')">Пауза</button>
          <button class="btn btn--ghost" type="button" @click="runAction('resume')">Запуск</button>
          <button class="btn btn--ghost" type="button" @click="runAction('archive')">Архив</button>
          <button class="btn btn--ghost" type="button" @click="runAction('unarchive')">Из архива</button>
          <button class="btn btn--ghost" type="button" @click="runAction('delete')">Удалить</button>
          <input v-model.number="budgetRub" type="number" min="1" step="1" placeholder="Бюджет ₽" />
          <button class="btn btn--accent" type="button" @click="runAction('set_budget')">Бюджет</button>
        </div>

        <section class="panel">
          <div v-if="loading" class="empty">Загрузка…</div>
          <div v-else-if="items.length" class="list">
            <div v-for="item in items" :key="item.id" class="list-row">
              <div class="check-row">
                <input
                  type="checkbox"
                  :checked="selected.has(item.id)"
                  @change="toggle(item.id, $event.target.checked)"
                />
                <div>
                  <strong>{{ item.name || item.campaign_id }}</strong>
                  <small>
                    {{ item.client_login }} · {{ item.state }} / {{ item.status }}
                    <template v-if="item.daily_budget_kopecks != null">
                      · {{ formatMoneyKopecks(item.daily_budget_kopecks) }}/день
                    </template>
                  </small>
                </div>
              </div>
              <div class="row-actions">
                <span class="chip">{{ item.campaign_id }}</span>
                <RouterLink class="btn btn--ghost" :to="`/cabinet/campaigns/${item.id}`">Группы</RouterLink>
                <button class="btn btn--ghost" type="button" @click="openEdit(item)">Изменить</button>
              </div>
            </div>
          </div>
          <p v-else class="empty">Кампаний нет. Создайте новую или нажмите «Синк кампаний».</p>

          <div class="pager">
            <span class="hint">{{ pageLabel }}</span>
            <div class="row-actions">
              <button class="btn btn--ghost" type="button" :disabled="page <= 1 || loading" @click="goPage(page - 1)">
                Назад
              </button>
              <button
                class="btn btn--ghost"
                type="button"
                :disabled="page >= pageCount || loading"
                @click="goPage(page + 1)"
              >
                Вперёд
              </button>
            </div>
          </div>
        </section>

        <section v-if="editItem" class="panel">
          <h2>Редактирование</h2>
          <div class="filters">
            <div class="field">
              <label>Название</label>
              <input v-model="editForm.name" type="text" />
            </div>
            <div class="field">
              <label>Бюджет ₽/день</label>
              <input v-model.number="editForm.daily_budget_rub" type="number" min="1" step="1" />
            </div>
            <button class="btn btn--accent" type="button" :disabled="saving" @click="saveEdit">Сохранить</button>
            <button class="btn btn--ghost" type="button" @click="editItem = null">Отмена</button>
          </div>
        </section>

        <section class="panel">
          <h2>Лог действий</h2>
          <p class="hint">Что ушло в Директ: пауза, бюджет, создание кампаний.</p>
          <div v-if="logs.length" class="list">
            <div v-for="log in logs" :key="log.id" class="list-row">
              <div>
                <strong>{{ actionLogTitle(log) }}</strong>
                <small>
                  {{ log.status }} · {{ log.created_at }}
                  <template v-if="actionLogDetail(log)"> · {{ actionLogDetail(log) }}</template>
                </small>
              </div>
            </div>
          </div>
          <p v-else class="empty">Пока пусто — действия появятся после операций с кампаниями.</p>
        </section>
      </div>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import CabinetNav from '../components/CabinetNav.vue'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { errorText, formatMoneyKopecks, webApi } from '../api/web'
import { getSession } from '../composables/useSession'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'

useHead('Кампании — AiMarketing', true)

const router = useRouter()
const items = ref([])
const total = ref(0)
const accounts = ref([])
const logs = ref([])
const loading = ref(true)
const syncing = ref(false)
const creating = ref(false)
const saving = ref(false)
const accountId = ref('')
const state = ref('')
const search = ref('')
const selected = ref(new Set())
const budgetRub = ref(null)
const page = ref(1)
const pageSize = ref(50)
const editItem = ref(null)
const editForm = reactive({ name: '', daily_budget_rub: 0 })
const createForm = reactive({
  account_id: '',
  name: '',
  daily_budget_rub: 300,
  start_date: new Date().toISOString().slice(0, 10),
})

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const pageLabel = computed(() => {
  if (!total.value) return 'Всего: 0'
  const from = (page.value - 1) * pageSize.value + 1
  const to = Math.min(page.value * pageSize.value, total.value)
  return `${from}–${to} из ${total.value}`
})

function parseJsonSafe(raw) {
  if (raw == null) return null
  if (typeof raw === 'object') return raw
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

function actionLogTitle(log) {
  const map = {
    pause: 'Пауза',
    resume: 'Запуск',
    set_budget: 'Бюджет',
    archive: 'Архив',
    unarchive: 'Из архива',
    delete: 'Удаление',
    update: 'Обновление',
    create: 'Создание',
  }
  return map[log.action] || log.action || 'Действие'
}

function actionLogDetail(log) {
  const payload = parseJsonSafe(log.payload)
  const result = parseJsonSafe(log.result)
  const parts = []
  if (payload?.name) parts.push(payload.name)
  if (payload?.campaign_ids?.length) parts.push(`камп.: ${payload.campaign_ids.length}`)
  if (payload?.daily_budget_rub) parts.push(`${payload.daily_budget_rub} ₽/день`)
  if (result?.applied != null) parts.push(`применено ${result.applied}`)
  return parts.join(' · ')
}

async function load() {
  if (!getSession()) {
    router.replace('/login')
    return
  }
  loading.value = true
  try {
    const [camp, me, actions] = await Promise.all([
      webApi.campaigns({
        account_id: accountId.value || undefined,
        state: state.value || undefined,
        search: search.value || undefined,
        limit: pageSize.value,
        offset: (page.value - 1) * pageSize.value,
      }),
      webApi.me(),
      webApi.actionLogs(),
    ])
    items.value = camp.items || []
    total.value = camp.total || 0
    accounts.value = (me.direct || []).filter((a) => a.enabled)
    logs.value = actions.items || []
    selected.value = new Set()
    if (page.value > pageCount.value) page.value = pageCount.value
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loading.value = false
  }
}

function onFilter() {
  page.value = 1
  load()
}

function goPage(n) {
  page.value = Math.min(pageCount.value, Math.max(1, n))
  load()
}

function toggle(id, on) {
  const next = new Set(selected.value)
  if (on) next.add(id)
  else next.delete(id)
  selected.value = next
}

async function syncAll() {
  syncing.value = true
  try {
    const results = await Promise.allSettled(accounts.value.map((a) => webApi.syncAccount(a.id)))
    const failed = results.filter((r) => r.status === 'rejected')
    if (failed.length) pushToast(`${errorText(failed[0].reason)} (кабинетов с ошибкой: ${failed.length})`, 'error')
    else pushToast('Кампании обновлены')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    syncing.value = false
  }
}

async function createCampaign() {
  if (!createForm.account_id || !createForm.name.trim()) {
    pushToast('Укажите аккаунт и название', 'error')
    return
  }
  creating.value = true
  try {
    await webApi.createCampaign({
      account_id: Number(createForm.account_id),
      name: createForm.name.trim(),
      daily_budget_rub: Number(createForm.daily_budget_rub),
      start_date: createForm.start_date || undefined,
    })
    pushToast('Кампания создана в Директе')
    createForm.name = ''
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    creating.value = false
  }
}

function openEdit(item) {
  editItem.value = item
  editForm.name = item.name || ''
  editForm.daily_budget_rub = item.daily_budget_kopecks != null ? Math.round(item.daily_budget_kopecks / 100) : 300
}

async function saveEdit() {
  if (!editItem.value) return
  saving.value = true
  try {
    await webApi.updateCampaign(editItem.value.id, {
      name: editForm.name.trim(),
      daily_budget_rub: Number(editForm.daily_budget_rub),
    })
    pushToast('Кампания обновлена')
    editItem.value = null
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    saving.value = false
  }
}

async function runAction(action) {
  const ids = [...selected.value]
  if (!ids.length) return
  if (action === 'delete') {
    const ok = window.confirm(`Удалить ${ids.length} кампаний в Директе? Это необратимо.`)
    if (!ok) return
  }
  if (action === 'archive') {
    const ok = window.confirm(`Архивировать ${ids.length} кампаний?`)
    if (!ok) return
  }
  try {
    const payload = { campaign_ids: ids, action }
    if (action === 'set_budget') payload.daily_budget_rub = Number(budgetRub.value)
    const res = await webApi.bulkAction(payload)
    pushToast(`Готово: ${res.applied} (${res.status})`)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

onMounted(load)
</script>
