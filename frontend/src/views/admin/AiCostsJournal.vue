<template>
  <div class="adm-dash" :class="{ 'is-loading': loading && data }">
    <section class="panel">
      <div class="adm-costs__filters">
        <input v-model="userInput" class="search-input adm-costs__search" type="search" placeholder="Почта или #id" aria-label="Пользователь" />
        <UiSelect v-model="task" :options="taskOptions" size="sm" aria-label="Задача" />
        <UiSelect v-model="model" :options="modelOptions" size="sm" aria-label="Модель" />
        <UiSelect v-model="sort" :options="SORTS" size="sm" aria-label="Порядок" />
        <label class="adm-costs__check"><input v-model="subs" type="checkbox" /> Только подмены</label>
        <span v-if="data" class="adm-charts__total">
          {{ num(data.total.calls) }} {{ plural(data.total.calls, 'вызов', 'вызова', 'вызовов') }} · {{ rubAi(data.total.cost_rub) }}
        </span>
      </div>

      <p v-if="error && !data" class="empty">{{ error }}</p>
      <p v-else-if="!data" class="empty">Загружаем…</p>
      <p v-else-if="!data.items.length" class="empty">Вызовов под эти условия нет.</p>
      <div v-else v-scroll-x class="table-scroll">
        <table class="table adm-costs__table">
          <thead>
            <tr>
              <th>Когда</th>
              <th>Кто</th>
              <th>Задача</th>
              <th>Модель</th>
              <th class="num" v-tip="'Вход · из них из кэша · выход'">Токены</th>
              <th class="num">Единиц</th>
              <th class="num">₽</th>
              <th class="num">Время</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in data.items" :key="r.id">
              <td>{{ formatDateTime(r.created_at) }}</td>
              <td>
                <RouterLink v-if="r.user_id" :to="{ path: `${ADMIN_BASE}/users`, query: { user: r.user_id } }">{{ r.email || `#${r.user_id}` }}</RouterLink>
                <span v-else class="chip adm-costs__chip">лендинг</span>
              </td>
              <td>{{ r.task_label }}</td>
              <td>
                {{ r.model || KIND_LABELS[r.kind] || '—' }}
                <small v-if="r.fallback_from" class="cell-note tone-warn" v-tip="r.fallback_reason || null"> · вместо {{ r.fallback_from }}</small>
                <small v-else-if="r.class_name" class="cell-note"> · {{ r.class_name }}</small>
              </td>
              <td class="num">
                <template v-if="r.kind === 'model'">
                  {{ tokens(r.tokens_in) }}<small v-if="r.tokens_cached" class="cell-note"> · {{ tokens(r.tokens_cached) }}</small> · {{ tokens(r.tokens_out) }}
                </template>
                <template v-else>—</template>
              </td>
              <td class="num">{{ r.units ? num(r.units, 2) : '—' }}</td>
              <td class="num" v-tip="r.estimated ? 'Токены оценены — провайдер их не вернул' : null">
                {{ rubAi(r.cost_rub) }}<span v-if="r.estimated" class="cell-note">≈</span>
              </td>
              <td class="num">{{ r.duration_ms != null ? `${num(r.duration_ms / 1000, 1)} с` : '—' }}</td>
              <td>
                <button v-if="r.chat_id" class="btn btn--ghost btn--sm" type="button" @click="openChat(r.chat_id)">
                  <MessagesSquare aria-hidden="true" /> Чат
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <nav v-if="data && data.pages > 1" class="adm-costs__pages" aria-label="Страницы">
        <button class="btn btn--ghost btn--sm" type="button" :disabled="data.page <= 1" @click="page = data.page - 1">← Назад</button>
        <span>Страница {{ data.page }} из {{ data.pages }}</span>
        <button class="btn btn--ghost btn--sm" type="button" :disabled="data.page >= data.pages" @click="page = data.page + 1">Дальше →</button>
      </nav>
    </section>

    <AdminChatModal :chat="reading" @close="reading = null" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { MessagesSquare } from '@lucide/vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import AdminChatModal from './AdminChatModal.vue'
import { ADMIN_BASE } from './adminNav'
import { readChat, tokens, useCostsTab } from './aiCosts'
import { errorText } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import { formatDateTime, num, plural, rubAi } from '../../utils/format'

/** Журнал: отдельные вызовы с фильтрами по человеку, задаче, модели и подменам; можно сортировать по цене. */
const props = defineProps({ params: { type: Object, required: true } })
const emit = defineEmits(['period'])

const SORTS = [
  { value: 'time', label: 'Сначала новые' },
  { value: 'cost', label: 'Сначала дорогие' },
]
const KIND_LABELS = { wordstat: 'Вордстат', image: 'Картинки' }

const userInput = ref('')
const user = ref('')
const task = ref('')
const model = ref('')
const sort = ref('time')
const subs = ref(false)
const page = ref(1)

let timer = null
watch(userInput, (v) => {
  clearTimeout(timer)
  timer = setTimeout(() => (user.value = v.trim()), 350)
})
onBeforeUnmount(() => clearTimeout(timer))

const params = computed(() => props.params)
watch([params, user, task, model, sort, subs], () => (page.value = 1), { deep: true })

const { data, loading, error } = useCostsTab(
  'journal',
  params,
  () => ({ user: user.value, task: task.value, model: model.value, sort: sort.value, subs: subs.value, page: page.value }),
  (p) => emit('period', p),
)

const taskOptions = computed(() => [{ value: '', label: 'Все задачи' }, ...(data.value?.options.tasks || [])])
const modelOptions = computed(() => [{ value: '', label: 'Все модели' }, ...(data.value?.options.models || []).map((m) => ({ value: m, label: m }))])

const reading = ref(null)
async function openChat(id) {
  try {
    reading.value = await readChat(id)
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}
</script>
