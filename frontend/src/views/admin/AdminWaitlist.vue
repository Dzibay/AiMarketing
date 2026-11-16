<template>
  <section class="panel" id="site-waitlist">
    <div class="panel__head">
      <h2>Лист ожидания</h2>
      <span v-for="c in waitlist.channels" :key="c.id" class="chip">{{ c.label }}: {{ num(c.count) }}</span>
    </div>
    <p class="hint">Почты со страниц интеграций «скоро». Напишите им, когда раздел запустится.</p>
    <p v-if="!waitlist.items.length" class="empty">Заявок пока нет.</p>
    <div v-else v-scroll-x class="table-scroll">
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
              <button class="btn btn--ghost btn--xs" type="button" :disabled="rowBusy === w.id" @click="remove(w)">
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
import { onMounted, ref } from 'vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { formatDateTime, num } from '../../utils/format'

const waitlist = ref({ channels: [], items: [] })
const rowBusy = ref(null)

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
    waitlist.value = await adminApi.waitlist()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
})
</script>
