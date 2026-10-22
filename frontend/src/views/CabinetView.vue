<template>
  <div class="site">
    <SiteHeader solid />
    <main class="cabinet">
      <div class="wrap">
        <div v-if="loading" class="panel"><p class="empty">Загружаем кабинет…</p></div>

        <template v-else-if="me">
          <div class="cabinet__head">
            <div>
              <h1>Главная</h1>
              <p>
                {{ me.user.name || me.user.email || 'Аккаунт' }}
                <span v-if="me.user.email && me.user.name"> · {{ me.user.email }}</span>
              </p>
            </div>
            <button class="btn btn--ghost" type="button" @click="logout">Выйти</button>
          </div>

          <ConnectOffers
            :connections="me.connections"
            :metrika="me.metrika"
            :direct="me.direct"
          />

          <template v-if="me.connections.length">
            <section class="panel">
              <h2>Аккаунты Яндекса</h2>
              <p class="hint">Токены хранятся зашифрованно. Можно обновить список или отключить доступ.</p>
              <div class="list">
                <div v-for="item in me.connections" :key="item.id" class="list-row">
                  <div>
                    <strong>{{ item.display_name || item.yandex_login || item.yandex_uid }}</strong>
                    <small>@{{ item.yandex_login || item.yandex_uid }}</small>
                  </div>
                  <div class="row-actions">
                    <button class="btn btn--ghost" type="button" :disabled="busyId === item.id" @click="sync(item.id)">
                      Обновить
                    </button>
                    <button class="btn btn--ghost" type="button" :disabled="busyId === item.id" @click="remove(item.id)">
                      Отключить
                    </button>
                  </div>
                </div>
              </div>
            </section>

            <section class="panel">
              <h2>Счётчики Метрики</h2>
              <div v-if="me.metrika.length" class="list">
                <div
                  v-for="item in me.metrika"
                  :key="item.id"
                  class="list-row"
                  :class="{ 'is-off': !item.enabled }"
                >
                  <div>
                    <strong>{{ item.name || `Счётчик ${item.counter_id}` }}</strong>
                    <small>{{ item.site || `ID ${item.counter_id}` }}</small>
                  </div>
                  <div class="row-actions">
                    <span class="chip">{{ item.counter_id }}</span>
                    <button class="btn btn--ghost" type="button" @click="toggleMetrika(item)">
                      {{ item.enabled ? 'Выключить' : 'Включить' }}
                    </button>
                  </div>
                </div>
              </div>
              <p v-else class="empty">Счётчиков пока нет — нажмите «Обновить» у подключения.</p>
            </section>

            <section class="panel">
              <h2>Кабинеты Директа</h2>
              <div v-if="me.direct.length" class="list">
                <div
                  v-for="item in me.direct"
                  :key="item.id"
                  class="list-row"
                  :class="{ 'is-off': !item.enabled }"
                >
                  <div>
                    <strong>{{ item.name || item.client_login }}</strong>
                    <small>
                      {{ item.client_login }}
                      <template v-if="item.currency"> · {{ item.currency }}</template>
                    </small>
                  </div>
                  <div class="row-actions">
                    <button class="btn btn--ghost" type="button" @click="toggleDirect(item)">
                      {{ item.enabled ? 'Выключить' : 'Включить' }}
                    </button>
                  </div>
                </div>
              </div>
              <p v-else class="empty">
                Кабинетов пока нет. Если API Директа ещё не одобрен — появятся после доступа.
              </p>
            </section>
          </template>
        </template>
      </div>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import ConnectOffers from '../components/ConnectOffers.vue'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { errorText, webApi } from '../api/web'
import { clearSession, getSession } from '../composables/useSession'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'

useHead('Главная — AiMarketing', true)

const router = useRouter()
const me = ref(null)
const loading = ref(true)
const busyId = ref(null)

async function load() {
  if (!getSession()) {
    router.replace('/login')
    return
  }
  loading.value = true
  try {
    me.value = await webApi.me()
  } catch (err) {
    pushToast(errorText(err), 'error')
    router.replace('/login')
  } finally {
    loading.value = false
  }
}

async function sync(id) {
  busyId.value = id
  try {
    const stats = await webApi.syncConnection(id)
    pushToast(`Обновлено: Метрика ${stats.metrika}, Директ ${stats.direct}`)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busyId.value = null
  }
}

async function remove(id) {
  busyId.value = id
  try {
    await webApi.deleteConnection(id)
    pushToast('Подключение удалено')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busyId.value = null
  }
}

async function toggleMetrika(item) {
  try {
    await webApi.patchMetrika(item.id, !item.enabled)
    item.enabled = !item.enabled
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function toggleDirect(item) {
  try {
    await webApi.patchDirect(item.id, !item.enabled)
    item.enabled = !item.enabled
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

function logout() {
  clearSession()
  router.replace('/login')
}

onMounted(load)
</script>
