<template>
  <section v-if="visible.length" class="panel" id="projects">
    <div class="panel__head">
      <div>
        <h2>{{ limited ? 'Проекты по тарифу' : 'Счётчики в других аккаунтах' }}</h2>
        <p class="hint">
          <template v-if="limited">
            Проектов: {{ data.projects_used }} из {{ data.projects_limit }}.
            <template v-if="data.projects_used > data.projects_limit">
              Лишние проекты под замком: откройте их на тарифе выше или отправьте в архив — данные сохранятся.
            </template>
            <template v-else>
              Выберите, какие счётчики станут проектами, — остальные доступны на тарифе выше. Лишний проект можно
              отправить в архив, данные сохранятся.
            </template>
          </template>
          <template v-else>Эти счётчики уже подключены как проекты в другом аккаунте.</template>
        </p>
      </div>
      <RouterLink v-if="limited" class="btn btn--ghost" to="/cabinet/plan">Тарифы</RouterLink>
    </div>
    <div class="list">
      <div v-for="c in visible" :key="c.counter_id" class="list-row" :class="{ 'is-off': OFF.has(c.state) }">
        <div>
          <strong>{{ c.name || c.site || `Счётчик ${c.counter_id}` }}</strong>
          <small>
            № {{ c.counter_id }}
            <template v-if="c.site && c.name"> · {{ c.site }}</template>
            <template v-if="c.login"> · @{{ c.login }}</template>
          </small>
          <small v-if="c.state === 'taken'" class="sync-status is-warn">{{ c.note }}</small>
          <small v-else-if="isLocked(c)" class="sync-status is-warn">
            <Lock class="inline-icon" aria-hidden="true" /> проект сверх тарифа — закрыт
          </small>
          <small v-else-if="c.state === 'archived'" class="sync-status">проект в архиве</small>
        </div>
        <div class="row-actions">
          <button
            v-if="isLocked(c)"
            class="btn btn--ghost"
            type="button"
            :disabled="busy !== null"
            @click="archive(c)"
          >
            {{ busy === c.counter_id ? 'Убираем…' : 'В архив' }}
          </button>
          <RouterLink v-else-if="c.state === 'project'" class="btn btn--ghost" :to="`/w/${c.workspace_id}/summary`">
            Проект
          </RouterLink>
          <button
            v-else-if="c.state === 'available' || c.state === 'archived'"
            class="btn btn--ghost"
            type="button"
            :disabled="busy !== null"
            @click="choose(c)"
          >
            {{ busy === c.counter_id ? 'Создаём…' : c.state === 'archived' ? 'Вернуть из архива' : 'Сделать проектом' }}
          </button>
          <RouterLink v-else-if="c.state === 'locked'" class="chip chip--lock" to="/cabinet/plan">
            <Lock aria-hidden="true" /> на тарифе выше
          </RouterLink>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Lock } from '@lucide/vue'
import { errorText, wsApi } from '../api/web'
import { askConfirm } from '../composables/useConfirm'
import { pushToast } from '../composables/useToasts'
import { loadWorkspaces, useWorkspaces } from '../composables/useWorkspaces'

/**
 * Выбор счётчиков под лимит проектов тарифа: сверх лимита проекты сами не создаются.
 * Без лимита показываем только счётчики, занятые в других аккаунтах, — чтобы было понятно, куда они делись.
 */
const emit = defineEmits(['changed'])

const OFF = new Set(['locked', 'taken'])
const { workspaces } = useWorkspaces()
const lockedIds = computed(() => new Set(workspaces.value.filter((w) => w.locked).map((w) => w.id)))
const data = ref({ items: [], projects_limit: null, projects_used: 0 })
const busy = ref(null)

const limited = computed(() => data.value.projects_limit !== null && data.value.projects_limit !== undefined)
const visible = computed(() =>
  limited.value ? data.value.items : data.value.items.filter((c) => c.state === 'taken'),
)

/** Проект есть, но сверх лимита тарифа (тариф понизили): открыть нельзя, можно убрать в архив. */
function isLocked(c) {
  return c.state === 'project' && lockedIds.value.has(c.workspace_id)
}

async function archive(c) {
  const ok = await askConfirm(
    `Отправить проект «${c.name || c.site || c.counter_id}» в архив? Данные сохранятся, вернуть его можно здесь же.`,
  )
  if (!ok) return
  busy.value = c.counter_id
  try {
    await wsApi.update(c.workspace_id, { archived: true })
    pushToast('Проект в архиве')
    await Promise.all([load(), loadWorkspaces(true).catch(() => null)])
    emit('changed')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = null
  }
}

async function load() {
  try {
    data.value = await wsApi.counters()
  } catch {
    // Не критично: список счётчиков есть ниже, выбор проектов появится при следующей загрузке.
  }
}

async function choose(c) {
  busy.value = c.counter_id
  try {
    const ws = await wsApi.fromCounter(c.counter_id)
    pushToast(`Проект «${ws.name || c.name || c.counter_id}» готов`)
    await Promise.all([load(), loadWorkspaces(true).catch(() => null)])
    emit('changed')
  } catch (err) {
    pushToast(errorText(err), 'error')
    await load()
  } finally {
    busy.value = null
  }
}

onMounted(load)
defineExpose({ load })
</script>
