<template>
  <AppShell>
    <div class="cabinet__head">
      <div>
        <h1>Клиенты</h1>
        <p>
          Папки для сайтов: группируют пространства в переключателе счётчиков и в обзоре. Общий месячный бюджет клиента
          контролируется по сумме расходов всех его сайтов.
        </p>
      </div>
    </div>

    <section class="panel">
      <h2>Новый клиент</h2>
      <form class="filters" @submit.prevent="create">
        <div class="field field--wide">
          <label>Название</label>
          <input v-model.trim="draft.name" type="text" required maxlength="200" placeholder="ООО «Ромашка»" />
        </div>
        <div class="field">
          <label>Общий бюджет в месяц, ₽</label>
          <input v-model="draft.monthly_budget_rub" type="number" min="0" step="1000" placeholder="не задан" />
        </div>
        <button class="btn btn--accent" type="submit" :disabled="busy">Создать</button>
      </form>
    </section>

    <div v-if="!loaded" class="panel"><p class="empty">Загружаем…</p></div>

    <template v-else>
      <section v-for="client in activeClients" :key="client.id" class="panel folder">
        <div class="panel__head">
          <div>
            <h2>{{ client.name }}</h2>
            <p class="hint">
              {{ wsOf(client.id).length }} {{ plural(wsOf(client.id).length, 'сайт', 'сайта', 'сайтов') }}
              <template v-if="client.monthly_budget_rub"> · общий бюджет {{ rubles(client.monthly_budget_rub) }} в месяц</template>
            </p>
          </div>
          <div class="row-actions">
            <button class="btn btn--ghost" type="button" @click="startEdit(client)">
              {{ editId === client.id ? 'Отмена' : 'Изменить' }}
            </button>
            <button class="btn btn--ghost" type="button" :disabled="busy" @click="archive(client, true)">В архив</button>
          </div>
        </div>

        <form v-if="editId === client.id" class="form-grid" @submit.prevent="saveEdit(client)">
          <div class="field">
            <label>Название</label>
            <input v-model.trim="edit.name" type="text" required maxlength="200" />
          </div>
          <div class="field">
            <label>Общий бюджет в месяц, ₽</label>
            <input v-model="edit.monthly_budget_rub" type="number" min="0" step="1000" placeholder="не задан" />
          </div>
          <div class="field field--wide">
            <label>Заметки</label>
            <textarea v-model="edit.notes" rows="3" maxlength="5000" placeholder="Контакты, договорённости, особенности"></textarea>
          </div>
          <div class="form-actions">
            <button class="btn btn--accent" type="submit" :disabled="busy">Сохранить</button>
            <button class="btn btn--ghost btn--danger" type="button" :disabled="busy" @click="remove(client)">
              Удалить клиента
            </button>
          </div>
        </form>
        <p v-else-if="client.notes" class="folder__notes">{{ client.notes }}</p>

        <div v-if="wsOf(client.id).length" class="list">
          <div v-for="w in wsOf(client.id)" :key="w.id" class="list-row">
            <div>
              <RouterLink :to="`/w/${w.id}/summary`"><strong>{{ w.name }}</strong></RouterLink>
              <small>
                № {{ w.counter_id }} · {{ w.campaigns_count }} {{ plural(w.campaigns_count, 'кампания', 'кампании', 'кампаний') }}
                <template v-if="w.monthly_budget_rub"> · бюджет сайта {{ rubles(w.monthly_budget_rub) }}</template>
              </small>
            </div>
            <div class="row-actions">
              <button class="btn btn--ghost" type="button" :disabled="busy" @click="assign(w, null)">Убрать из папки</button>
            </div>
          </div>
        </div>
        <p v-else class="empty">В папке пока нет сайтов.</p>

        <div v-if="unassigned.length" class="filters folder__add">
          <div class="field field--wide">
            <label>Добавить сайт</label>
            <select @change="assignFromSelect($event, client.id)">
              <option value="">Выберите пространство без клиента</option>
              <option v-for="w in unassigned" :key="w.id" :value="w.id">{{ w.name }} · № {{ w.counter_id }}</option>
            </select>
          </div>
        </div>
      </section>

      <section class="panel">
        <h2>Без клиента</h2>
        <p class="hint">Пространства вне папок — в переключателе они идут последней группой.</p>
        <div v-if="unassigned.length" class="list">
          <div v-for="w in unassigned" :key="w.id" class="list-row">
            <div>
              <RouterLink :to="`/w/${w.id}/summary`"><strong>{{ w.name }}</strong></RouterLink>
              <small>№ {{ w.counter_id }} · {{ w.campaigns_count }} {{ plural(w.campaigns_count, 'кампания', 'кампании', 'кампаний') }}</small>
            </div>
            <div v-if="activeClients.length" class="row-actions">
              <select :value="''" :disabled="busy" @change="assignFromSelect($event, null, w)">
                <option value="">В папку…</option>
                <option v-for="c in activeClients" :key="c.id" :value="c.id">{{ c.name }}</option>
              </select>
            </div>
          </div>
        </div>
        <p v-else class="empty">
          {{ workspaces.length ? 'Все сайты разложены по клиентам.' : 'Пространств пока нет — подключите Метрику в «Аккаунтах».' }}
        </p>
      </section>

      <section v-if="archivedClients.length" class="panel">
        <h2>Архив</h2>
        <div class="list">
          <div v-for="c in archivedClients" :key="c.id" class="list-row is-off">
            <div>
              <strong>{{ c.name }}</strong>
              <small>{{ wsOf(c.id).length }} {{ plural(wsOf(c.id).length, 'сайт', 'сайта', 'сайтов') }}</small>
            </div>
            <div class="row-actions">
              <button class="btn btn--ghost" type="button" :disabled="busy" @click="archive(c, false)">Вернуть</button>
              <button class="btn btn--ghost btn--danger" type="button" :disabled="busy" @click="remove(c)">Удалить</button>
            </div>
          </div>
        </div>
      </section>
    </template>
  </AppShell>
</template>

<script setup>
import { askConfirm } from '../composables/useConfirm'
import { computed, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppShell from '../components/AppShell.vue'
import { errorText, webApi, wsApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { useWorkspaces } from '../composables/useWorkspaces'
import { useHead } from '../utils/useHead'
import { plural, rubles } from '../utils/format'

useHead('Клиенты — AiMarketing', true)

const { workspaces, clients, loaded, loadWorkspaces, patchWorkspaceInList } = useWorkspaces()
const busy = ref(false)
const draft = reactive({ name: '', monthly_budget_rub: '' })
const editId = ref(null)
const edit = reactive({ name: '', monthly_budget_rub: '', notes: '' })

const activeClients = computed(() => clients.value.filter((c) => !c.archived))
const archivedClients = computed(() => clients.value.filter((c) => c.archived))
const unassigned = computed(() => workspaces.value.filter((w) => !w.client_id))

function wsOf(clientId) {
  return workspaces.value.filter((w) => w.client_id === clientId)
}

function budgetValue(raw) {
  if (raw === '' || raw === null || raw === undefined) return null
  return Number(raw)
}

async function run(fn, done) {
  busy.value = true
  try {
    await fn()
    if (done) pushToast(done)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

function create() {
  return run(async () => {
    await webApi.createClient({ name: draft.name, monthly_budget_rub: budgetValue(draft.monthly_budget_rub) })
    draft.name = ''
    draft.monthly_budget_rub = ''
    await loadWorkspaces(true)
  }, 'Клиент создан')
}

function startEdit(client) {
  if (editId.value === client.id) {
    editId.value = null
    return
  }
  editId.value = client.id
  edit.name = client.name
  edit.monthly_budget_rub = client.monthly_budget_rub ?? ''
  edit.notes = client.notes || ''
}

function saveEdit(client) {
  return run(async () => {
    await webApi.patchClient(client.id, {
      name: edit.name,
      notes: edit.notes,
      monthly_budget_rub: budgetValue(edit.monthly_budget_rub),
    })
    editId.value = null
    await loadWorkspaces(true)
  }, 'Сохранено')
}

function archive(client, archived) {
  return run(async () => {
    await webApi.patchClient(client.id, { archived })
    await loadWorkspaces(true)
  }, archived ? 'Клиент в архиве' : 'Клиент возвращён')
}

async function remove(client) {
  if (!await askConfirm(`Удалить клиента «${client.name}»? Его сайты останутся, просто без папки.`)) return
  return run(async () => {
    await webApi.deleteClient(client.id)
    editId.value = null
    await loadWorkspaces(true)
  }, 'Клиент удалён')
}

function assign(ws, clientId) {
  return run(async () => {
    const updated = await wsApi.update(ws.id, { client_id: clientId })
    patchWorkspaceInList(updated)
  })
}

function assignFromSelect(event, clientId, ws = null) {
  const value = Number(event.target.value || 0)
  event.target.value = ''
  if (!value) return
  if (ws) return assign(ws, value)
  const target = workspaces.value.find((w) => w.id === value)
  if (target) assign(target, clientId)
}

loadWorkspaces(true).catch((err) => pushToast(errorText(err), 'error'))
</script>
