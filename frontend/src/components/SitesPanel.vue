<template>
  <section class="sites">
    <h2 class="sites__h">
      Сайты <span class="sites__n">{{ sites.length }}</span>
      <small v-if="limited">
        <template v-if="planName">тариф «{{ planName }}»: </template>{{ data.projects_used }} из {{ data.projects_limit }}
      </small>
    </h2>

    <div v-if="sites.length" class="ccard sites__list">
      <div v-for="w in sites" :key="w.id" class="prow" :class="{ 'prow--wrap': w.locked }">
        <div class="prow__main">
          <b class="site-name">
            <span class="sdot" :class="`sdot--${tone(w)}`" aria-hidden="true"></span>{{ w.name }}
          </b>
          <p>{{ siteLine(w) }}</p>
        </div>
        <div class="prow__acts">
          <template v-if="w.locked">
            <span class="st st--off"><Lock :size="14" aria-hidden="true" />Не входит в тариф</span>
            <RouterLink class="btn btn--ghost" to="/cabinet/plan">Тарифы</RouterLink>
          </template>
          <template v-else>
            <span v-if="w.counter_ok === false" class="st st--warn">
              <TriangleAlert :size="14" aria-hidden="true" />Счётчик недоступен
            </span>
            <RouterLink class="btn btn--ghost" :to="`/w/${w.id}/summary`">Открыть</RouterLink>
          </template>
          <details class="bulk-more site-more" @toggle="onMenuToggle">
            <summary class="btn btn--ghost sq" :aria-label="`Действия с сайтом ${w.name}`">
              <Ellipsis :size="16" aria-hidden="true" />
            </summary>
            <div class="bulk-more__menu" role="menu" @click="closeMenu">
              <RouterLink v-if="!w.locked" class="menu-item" role="menuitem" :to="`/w/${w.id}/settings`">
                <Settings aria-hidden="true" />Настройки
              </RouterLink>
              <button v-else class="menu-item" type="button" role="menuitem" :disabled="busy !== null" @click="archive(w)">
                <Archive aria-hidden="true" />В архив
              </button>
              <button
                v-if="w.counter_id"
                class="menu-item is-danger"
                type="button"
                role="menuitem"
                :disabled="busy !== null"
                @click="remove(w)"
              >
                <Trash2 aria-hidden="true" />Удалить сайт
              </button>
            </div>
          </details>
        </div>
      </div>
    </div>
    <p v-else class="empty">
      Сайтов пока нет. Сайт — это счётчик Метрики: подключите Яндекс с доступом к Метрике, и сайты появятся здесь.
    </p>

    <details v-if="hidden.length" class="ccard removed">
      <summary class="removed__sum">
        <ChevronRight class="removed__chev" :size="16" aria-hidden="true" />
        Удалённые сайты <span class="sites__n">{{ hidden.length }}</span>
      </summary>
      <div v-for="h in hidden" :key="h.counter_id" class="prow">
        <div class="prow__main">
          <b>{{ h.name || h.site || `Счётчик ${h.counter_id}` }}</b>
          <p>{{ removedLine(h) }}</p>
        </div>
        <div class="prow__acts">
          <button class="btn btn--ghost" type="button" :disabled="busy !== null" @click="restore(h)">
            <Undo2 :size="14" aria-hidden="true" />{{ busy === `h${h.counter_id}` ? 'Возвращаем…' : 'Вернуть' }}
          </button>
        </div>
      </div>
    </details>

    <div v-if="adding" class="ccard add-box">
      <div class="add-box__head">
        <h3>Какой сайт добавить?</h3>
        <p class="hint">
          Это счётчики Метрики из подключённых аккаунтов Яндекса, которые ещё не стали сайтами.
          <template v-if="limited && freeSlots > 0">
            На тарифе<template v-if="planName"> «{{ planName }}»</template> можно добавить ещё {{ freeSlots }}.
          </template>
        </p>
      </div>
      <div v-for="c in candidates" :key="c.counter_id" class="prow" :class="{ 'is-off': c.state !== 'available' && c.state !== 'archived' }">
        <div class="prow__main">
          <b>{{ c.site || c.name || `Счётчик ${c.counter_id}` }}</b>
          <p>
            <template v-if="c.state === 'taken'">{{ c.note || 'Уже подключён в другом аккаунте' }}</template>
            <template v-else-if="c.state === 'archived'">В архиве — вернётся со всей историей</template>
            <template v-else>счётчик {{ c.counter_id }}<template v-if="c.name && c.site"> · {{ c.name }}</template></template>
          </p>
        </div>
        <div class="prow__acts">
          <button
            v-if="c.state === 'available' || c.state === 'archived'"
            class="btn"
            type="button"
            :disabled="busy !== null"
            @click="add(c)"
          >
            {{ busy === `c${c.counter_id}` ? 'Добавляем…' : c.state === 'archived' ? 'Вернуть' : 'Добавить' }}
          </button>
          <RouterLink v-else-if="c.state === 'locked'" class="chip chip--lock" to="/cabinet/plan">
            <Lock aria-hidden="true" /> на тарифе выше
          </RouterLink>
          <button
            class="btn btn--ghost sq"
            type="button"
            :disabled="busy !== null"
            :aria-label="`Не показывать ${c.site || c.name || c.counter_id}`"
            title="Не показывать"
            @click="dismiss(c)"
          >
            <EyeOff :size="15" aria-hidden="true" />
          </button>
        </div>
      </div>
      <p v-if="!candidates.length" class="add-box__empty">
        Все счётчики уже подключены. Новый сайт появится здесь, когда вы добавите его счётчик в Метрике — или
        подключите другой аккаунт Яндекса.
      </p>
      <div class="add-box__foot">
        <YandexConnectButton
          purpose="connect"
          label="Другой аккаунт Яндекса"
          variant="ghost"
          :wide="false"
          return-to="/cabinet/connections"
        />
        <button class="btn btn--ghost" type="button" @click="adding = false">Отмена</button>
      </div>
    </div>
    <div v-else class="form-actions sites__add">
      <button class="btn" type="button" @click="openAdd"><Plus :size="15" aria-hidden="true" />Добавить сайт</button>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { Archive, ChevronRight, Ellipsis, EyeOff, Lock, Plus, Settings, Trash2, TriangleAlert, Undo2 } from '@lucide/vue'
import YandexConnectButton from './YandexConnectButton.vue'
import { errorText, wsApi } from '../api/web'
import { askConfirm } from '../composables/useConfirm'
import { useExpert } from '../composables/useExpert'
import { useProfile } from '../composables/useProfile'
import { dismissCounter, removeSite, restoreSite, sitesVersion } from '../composables/useSiteRemoval'
import { pushToast } from '../composables/useToasts'
import { loadWorkspaces, useWorkspaces } from '../composables/useWorkspaces'
import { plural } from '../utils/format'

/**
 * Сайты на «Подключениях»: проекты (счётчики Метрики) — «Открыть» и меню с настройками и удалением,
 * «Удалённые сайты» с возвратом и «Добавить сайт» — выбор счётчика под лимит тарифа. Счётчики, занятые
 * в другом аккаунте или сверх тарифа, видны в том же списке; ненужные можно не показывать.
 */
const emit = defineEmits(['changed'])

const { expert } = useExpert()
const { workspaces } = useWorkspaces()
const { subscription } = useProfile()
const data = ref({ items: [], hidden: [], projects_limit: null, projects_used: 0 })
const adding = ref(false)
const busy = ref(null)

const sites = computed(() => workspaces.value.filter((w) => !w.archived))
const hidden = computed(() => data.value.hidden || [])
const limited = computed(() => data.value.projects_limit !== null && data.value.projects_limit !== undefined)
const planName = computed(() => subscription.value?.plan?.name || '')
const freeSlots = computed(() => Math.max(0, (data.value.projects_limit || 0) - (data.value.projects_used || 0)))
// Свободные — сверху, затем архив, тариф выше и занятые в других аккаунтах.
const ORDER = { available: 0, archived: 1, locked: 2, taken: 3 }
const candidates = computed(() =>
  data.value.items.filter((c) => c.state in ORDER).sort((a, b) => ORDER[a.state] - ORDER[b.state]),
)

function tone(w) {
  if (w.locked) return 'off'
  return w.counter_ok === false ? 'warn' : 'ok'
}

function siteLine(w) {
  const n = w.campaigns_count || 0
  const parts = []
  if (expert.value && w.counter_id) parts.push(`счётчик ${w.counter_id}`)
  parts.push(n ? `${n} ${plural(n, 'кампания', 'кампании', 'кампаний')}` : 'кампаний пока нет')
  if (w.client_name) parts.push(w.client_name)
  return parts.join(' · ')
}

function removedLine(h) {
  let state = 'Не показывается в «Добавить сайт»'
  if (h.had_project) {
    state = h.workspace_id ? 'Проект в архиве — вернётся со всей историей' : 'Удалён с данными — статистика загрузится заново'
  }
  return h.name && h.site && h.site !== h.name ? `${h.site} · ${state}` : state
}

async function load() {
  try {
    data.value = await wsApi.counters()
  } catch {
    // Не критично: список сайтов берётся из проектов, выбор счётчиков подтянется при следующем открытии.
  }
}

async function openAdd() {
  adding.value = true
  await load()
}

async function add(c) {
  busy.value = `c${c.counter_id}`
  try {
    const ws = await wsApi.fromCounter(c.counter_id)
    pushToast(`${ws.name || c.site || c.counter_id} добавлен. Загружаем кампании и статистику — обычно это занимает около минуты`)
    adding.value = false
    await Promise.all([load(), loadWorkspaces(true).catch(() => null)])
    emit('changed')
  } catch (err) {
    pushToast(errorText(err), 'error')
    await load()
  } finally {
    busy.value = null
  }
}

async function archive(w) {
  const ok = await askConfirm(`Отправить «${w.name}» в архив? Данные сохранятся, вернуть сайт можно через «Добавить сайт».`)
  if (!ok) return
  busy.value = `w${w.id}`
  try {
    await wsApi.update(w.id, { archived: true })
    pushToast('Сайт в архиве')
    await Promise.all([load(), loadWorkspaces(true).catch(() => null)])
    emit('changed')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = null
  }
}

// Удалили или вернули сайт — здесь или из тоста на другой странице.
watch(sitesVersion, () => {
  load()
  emit('changed')
})

async function remove(w) {
  busy.value = `w${w.id}`
  try {
    await removeSite({ counterId: w.counter_id, name: w.name })
  } finally {
    busy.value = null
  }
}

async function dismiss(c) {
  busy.value = `c${c.counter_id}`
  try {
    await dismissCounter({ counterId: c.counter_id, name: c.site || c.name })
  } finally {
    busy.value = null
  }
}

async function restore(h) {
  busy.value = `h${h.counter_id}`
  try {
    await restoreSite({ counterId: h.counter_id, name: h.name || h.site })
  } finally {
    busy.value = null
  }
}

// Меню «⋯»: одно открытое за раз, клик мимо закрывает.
function closeMenu(event) {
  const menu = event.currentTarget.closest('details')
  if (menu) menu.open = false
}

function closeOtherMenus(keep) {
  document.querySelectorAll('details.site-more[open]').forEach((menu) => {
    if (menu !== keep) menu.open = false
  })
}

function onMenuToggle(event) {
  if (event.currentTarget.open) closeOtherMenus(event.currentTarget)
}

function onDocClick(event) {
  if (!event.target.closest?.('details.site-more')) closeOtherMenus(null)
}

onMounted(() => {
  load()
  loadWorkspaces().catch(() => null)
  document.addEventListener('click', onDocClick)
})
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))
defineExpose({ load })
</script>

<style scoped>
.sites__h {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  margin: 28px 0 10px;
  font-size: 17px;
}
.sites__n {
  color: var(--faint);
  font-weight: 600;
}
.sites__h small {
  margin-left: auto;
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
}
.site-name {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.prow.is-off .prow__main {
  opacity: 0.6;
}
.prow__acts .btn {
  gap: 6px;
}
/* Меню «⋯» у нижних строк выходит за карточку — не обрезаем его (у строк нет фона, углы не страдают). */
.sites__list {
  overflow: visible;
}
.site-more .bulk-more__menu {
  right: 0;
  left: auto;
  min-width: 200px;
}
.site-more > summary {
  cursor: pointer;
}
.site-more a.menu-item {
  text-decoration: none;
}
.prow__acts .sq {
  padding-right: 10px;
  padding-left: 10px;
}
.removed {
  margin-top: 12px;
}
.removed__sum {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 18px;
  color: var(--muted);
  font-size: 14px;
  font-weight: 600;
  list-style: none;
  cursor: pointer;
}
.removed__sum::-webkit-details-marker {
  display: none;
}
.removed__chev {
  transition: transform 0.15s ease;
}
.removed[open] .removed__chev {
  transform: rotate(90deg);
}
.removed__sum + .prow {
  border-top: 1px solid var(--line);
}
.removed .prow__main {
  opacity: 0.75;
}
.sites__add {
  justify-content: flex-start;
  margin-top: 12px;
}
.sites__add .btn {
  gap: 6px;
}
.add-box {
  margin-top: 12px;
}
.add-box__head {
  padding: 16px 18px 6px;
}
.add-box__head h3 {
  margin: 0 0 4px;
  font-size: 15px;
}
.add-box__head + .prow {
  border-top: 1px solid var(--line);
}
.add-box__empty {
  margin: 0;
  padding: 12px 18px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 14px;
}
.add-box__foot {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 12px 18px 16px;
  border-top: 1px solid var(--line);
}
@media (max-width: 640px) {
  .prow--wrap {
    flex-wrap: wrap;
  }
  /* Кнопки строки переносятся под название и встают слева — меню открывается вправо от «⋯». */
  .site-more .bulk-more__menu {
    right: auto;
    left: 0;
  }
}
</style>
