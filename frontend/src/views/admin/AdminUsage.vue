<template>
  <section class="panel">
    <div class="panel__head">
      <h2>Расход сервиса</h2>
      <UiSelect
        v-if="data && data.months.length > 1"
        :model-value="data.month"
        :options="data.months.map((m) => ({ value: m, label: monthLabel(m) }))"
        :disabled="busy"
        aria-label="Месяц"
        @change="load"
      />
    </div>
    <p class="hint">
      Единицы — списано с лимитов пользователей. Себестоимость — сколько это стоило сервису по ценам, заданным в «ИИ и
      сервисах». Проверки подключения в админке сюда не попадают: они ничего не списывают.
    </p>
    <p v-if="!data" class="empty">Загружаем…</p>
    <template v-else>
      <div class="stats-grid">
        <div class="stat-card">
          <small>Вызовов</small>
          <strong>{{ num(data.total.calls) }}</strong>
        </div>
        <div class="stat-card">
          <small>Списано единиц</small>
          <strong>{{ num(data.total.units, 2) }}</strong>
        </div>
        <div class="stat-card">
          <small>Себестоимость</small>
          <strong>{{ rubAi(data.total.cost_rub) }}</strong>
        </div>
      </div>
      <p v-if="!data.total.calls" class="empty">В этом месяце платных вызовов не было.</p>
      <template v-else>
        <h3 class="subhead">По видам</h3>
        <div class="table-scroll">
          <table class="table usage-table">
            <thead>
              <tr>
                <th>Вид</th>
                <th class="num">Вызовов</th>
                <th class="num">Токенов</th>
                <th class="num">Единиц</th>
                <th class="num">₽</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in data.by_kind" :key="`${row.kind}:${row.model_class}`">
                <td>{{ row.label }}</td>
                <td class="num">{{ num(row.calls) }}</td>
                <td class="num">{{ row.kind === 'model' ? `${num(row.tokens_in)} / ${num(row.tokens_out)}` : '—' }}</td>
                <td class="num">{{ num(row.units, 2) }}</td>
                <td class="num">{{ rubAi(row.cost_rub) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <h3 class="subhead">Больше всего расходуют</h3>
        <div class="table-scroll">
          <table class="table usage-table">
            <thead>
              <tr>
                <th>Пользователь</th>
                <th class="num">Вызовов</th>
                <th class="num">Единиц</th>
                <th class="num">₽</th>
                <th class="num">Наценка</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in data.by_user" :key="row.user_id">
                <td>
                  {{ row.email || `#${row.user_id}` }}
                  <small v-if="row.name" class="cell-note">{{ row.name }}</small>
                </td>
                <td class="num">{{ num(row.calls) }}</td>
                <td class="num">{{ num(row.units, 2) }}</td>
                <td class="num">{{ rubAi(row.cost_rub) }}</td>
                <td class="num">{{ row.cost_rub > 0 ? `${num(row.units / row.cost_rub, 2)} ед./₽` : '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </template>
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import { num, rubAi } from '../../utils/format'

const data = ref(null)
const busy = ref(false)

function monthLabel(key) {
  const [y, m] = key.split('-').map(Number)
  const text = new Date(y, m - 1, 1).toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' }).replace(/\s*г\.$/, '')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

async function load(month = '') {
  busy.value = true
  try {
    data.value = await adminApi.usage(month)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

onMounted(() => load())
</script>
