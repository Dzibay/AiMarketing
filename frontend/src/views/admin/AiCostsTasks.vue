<template>
  <div class="adm-dash" :class="{ 'is-loading': loading && data }">
    <p v-if="error && !data" class="panel empty">{{ error }}</p>
    <div v-else-if="!data" class="panel"><p class="empty">Загружаем…</p></div>

    <template v-else>
      <section class="panel">
        <div class="panel__head">
          <h2>По задачам</h2>
          <span class="adm-charts__total">{{ rubAi(data.total_rub) }}</span>
        </div>
        <p v-if="!data.items.length" class="empty">За период вызовов нет.</p>
        <div v-else v-scroll-x class="table-scroll">
          <table class="table adm-costs__table">
            <thead>
              <tr>
                <th>Задача</th>
                <th class="num">Вызовов</th>
                <th class="num">Вход · выход</th>
                <th class="num">₽</th>
                <th class="num">За вызов</th>
                <th class="num">Единиц</th>
                <th class="num">Доля</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in data.items" :key="`${row.kind}:${row.task}`">
                <td>{{ row.label }}<span v-if="row.landing" class="chip adm-costs__chip">лендинг</span></td>
                <td class="num">{{ num(row.calls) }}</td>
                <td class="num">{{ row.kind === 'model' ? `${tokens(row.tokens_in)} · ${tokens(row.tokens_out)}` : '—' }}</td>
                <td class="num">{{ rubAi(row.cost_rub) }}</td>
                <td class="num">{{ rubAi(row.per_call_rub) }}</td>
                <td class="num">{{ num(row.units, 2) }}</td>
                <td class="num">
                  <span class="adm-costs__share"><i :style="{ width: `${row.share || 0}%` }"></i></span>{{ pct(row.share) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel__head">
          <h2>ИИ-чат</h2>
          <span class="adm-charts__total">{{ rubAi(chat.cost_rub) }}</span>
        </div>
        <div class="adm-costs__facts">
          <div><small>Цена вопроса</small><strong>{{ rubAi(chat.question_rub) }}</strong></div>
          <div><small>Вопросов</small><strong>{{ num(chat.questions) }}</strong></div>
          <div v-tip="'Чат на один вопрос может звать модель несколько раз: ответ, вызов инструментов, доработка'">
            <small>Вызовов на вопрос</small><strong>{{ num(chat.calls_per_question, 2) }}</strong>
          </div>
        </div>
        <h3 class="subhead">Самые дорогие чаты</h3>
        <p v-if="!chat.top.length" class="empty">За период вызовов из чатов нет.</p>
        <div v-else v-scroll-x class="table-scroll">
          <table class="table adm-costs__table">
            <thead>
              <tr>
                <th>Чат</th>
                <th>Пользователь</th>
                <th class="num">Вопросов</th>
                <th class="num">Вызовов</th>
                <th class="num">₽</th>
                <th class="num">Единиц</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in chat.top" :key="c.chat_id">
                <td class="adm-costs__title">{{ c.title || `Чат #${c.chat_id}` }}</td>
                <td>
                  <RouterLink v-if="c.user_id" :to="{ path: `${ADMIN_BASE}/users`, query: { user: c.user_id } }">{{ c.email || `#${c.user_id}` }}</RouterLink>
                </td>
                <td class="num">{{ num(c.questions) }}</td>
                <td class="num">{{ num(c.calls) }}</td>
                <td class="num">{{ rubAi(c.cost_rub) }}</td>
                <td class="num">{{ num(c.units, 2) }}</td>
                <td>
                  <button class="btn btn--ghost btn--sm" type="button" @click="openChat(c.chat_id)">
                    <MessagesSquare aria-hidden="true" /> Читать
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <AdminChatModal :chat="reading" @close="reading = null" />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { MessagesSquare } from '@lucide/vue'
import AdminChatModal from './AdminChatModal.vue'
import { ADMIN_BASE } from './adminNav'
import { readChat, tokens, useCostsTab } from './aiCosts'
import { errorText } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import { num, pct, rubAi } from '../../utils/format'

/** Задачи: расход по задачам; по чату — цена вопроса, вызовов на вопрос и десять самых дорогих чатов. */
const props = defineProps({ params: { type: Object, required: true } })
const emit = defineEmits(['period'])

const { data, loading, error } = useCostsTab('tasks', computed(() => props.params), null, (p) => emit('period', p))
const chat = computed(() => data.value.chat)
const reading = ref(null)

async function openChat(id) {
  try {
    reading.value = await readChat(id)
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}
</script>
