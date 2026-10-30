<template>
  <aside class="build panel" :class="{ 'is-open': open }" aria-label="Сборка кампании">
    <header class="build__head">
      <div class="build__title">
        <h3>Сборка кампании</h3>
        <p class="hint">{{ build.draft?.name || 'Новая кампания' }} · готово {{ doneCount }} из {{ steps.length }}</p>
      </div>
      <button class="build__close" type="button" aria-label="Скрыть сборку" @click="emit('close')">×</button>
    </header>

    <div class="build__progress" aria-hidden="true">
      <span v-for="s in steps" :key="s.key" :class="`is-${s.status}`" />
    </div>

    <ol class="build__steps">
      <li v-for="(s, i) in steps" :key="s.key" class="build__step" :class="`is-${s.status}`">
        <button type="button" :disabled="!s.action_id" :title="s.action_id ? 'Показать в чате' : ''" @click="emit('jump', s.action_id)">
          <span class="build__dot">{{ s.status === 'done' ? '✓' : i + 1 }}</span>
          <span class="build__step-label">{{ s.label }}</span>
          <small>{{ s.meta }}</small>
        </button>
      </li>
    </ol>

    <p v-if="hasPending" class="hint build__legend">
      <span class="build__swatch" aria-hidden="true" /> предложено ИИ и ждёт вашего решения
    </p>

    <section v-if="build.goals.length" class="build__block">
      <h4>Цели</h4>
      <ul class="build__facts">
        <li v-for="(g, i) in build.goals" :key="i" :class="{ 'is-pending': g.pending }">
          <span class="build__fact-key">{{ g.text }}</span>
          <span v-if="g.value">{{ g.value }}</span>
          <small v-if="g.note">{{ g.note }}</small>
        </li>
      </ul>
    </section>

    <section v-if="build.directions.length" class="build__block">
      <h4>Направления</h4>
      <ul class="build__facts build__facts--rows">
        <li v-for="(d, i) in build.directions" :key="i" :class="{ 'is-pending': d.pending }">
          <span class="build__fact-key">{{ d.text }}</span>
          <span v-if="d.value" class="build__num">{{ d.value }}</span>
          <small v-if="d.note">{{ d.note }}</small>
        </li>
      </ul>
    </section>

    <section class="build__block">
      <h4>
        Группы и объявления
        <small v-if="build.groups.length">
          {{ build.groups.length }} {{ plural(build.groups.length, 'группа', 'группы', 'групп') }} · {{ keywordCount }}
          {{ plural(keywordCount, 'фраза', 'фразы', 'фраз') }}
        </small>
      </h4>
      <p v-if="!build.groups.length" class="hint">Группы с фразами появятся после анализа спроса, на этапе «Группы и фразы».</p>
      <article v-for="g in build.groups" :key="g.name" class="build-group">
        <header class="build-group__head">
          <strong>{{ g.name }}</strong>
          <small>{{ g.keywords.length }} {{ plural(g.keywords.length, 'фраза', 'фразы', 'фраз') }}</small>
        </header>
        <ul v-if="g.keywords.length" class="build-group__keywords">
          <li v-for="(k, i) in shown(g)" :key="i" :class="{ 'is-pending': k.pending }" :title="k.note">
            {{ k.text }}<small v-if="k.value">{{ k.value }}</small>
          </li>
          <li v-if="g.keywords.length > KEYWORDS_SHOWN" class="build-group__more">
            <button type="button" @click="toggle(g.name)">
              {{ expanded[g.name] ? 'свернуть' : `ещё ${g.keywords.length - KEYWORDS_SHOWN}` }}
            </button>
          </li>
        </ul>
        <AdPreview
          v-if="g.titles.length || g.texts.length"
          :titles="g.titles.map((t) => t.text)"
          :texts="g.texts.map((t) => t.text)"
          :href="g.href"
          :site="site"
          :pending="[...g.titles, ...g.texts].some((t) => t.pending)"
        />
        <p v-else class="hint build-group__wait">Объявление появится на этапе «Объявления».</p>
      </article>
    </section>

    <section v-if="build.negatives.length" class="build__block">
      <h4>Минус-фразы <small>{{ build.negatives.length }}</small></h4>
      <ul class="build-group__keywords">
        <li v-for="(n, i) in build.negatives" :key="i" :class="{ 'is-pending': n.pending }">−{{ n.text }}</li>
      </ul>
    </section>

    <section v-for="b in factBlocks" :key="b.title" class="build__block">
      <h4>{{ b.title }}</h4>
      <ul class="build__facts">
        <li v-for="(r, i) in b.rows" :key="i" :class="{ 'is-pending': r.pending }">
          <span class="build__fact-key">{{ r.text }}</span>
          <span v-if="r.value">{{ r.value }}</span>
          <small v-if="r.note">{{ r.note }}</small>
        </li>
      </ul>
    </section>

    <div v-if="build.draft" class="build__draft" :class="`is-${build.draft.status}`">
      <template v-if="build.draft.status === 'done'">
        <strong>Черновик создан в Директе</strong>
        <RouterLink v-if="build.draft.campaign_row_id" :to="`/w/${wsId}/campaigns/${build.draft.campaign_row_id}`">
          Открыть кампанию
        </RouterLink>
      </template>
      <template v-else-if="build.draft.status === 'pending'">
        <strong>Карточка создания ждёт решения</strong>
        <button class="btn btn--accent" type="button" @click="emit('jump', build.draft.action_id)">Перейти к карточке</button>
      </template>
      <template v-else-if="build.draft.status === 'error'">
        <strong class="tone-bad">Черновик не создан</strong>
        <button class="ai-check__link" type="button" @click="emit('jump', build.draft.action_id)">Почему</button>
      </template>
      <strong v-else>Черновик создаётся…</strong>
    </div>
  </aside>
</template>

<script setup>
/**
 * Панель «Сборка кампании» рядом с чатом: этапы новой кампании и всё, что по ним согласовано, —
 * цели, направления, группы с фразами и предпросмотр объявлений. Данные — build из get_chat.
 */
import { computed, reactive } from 'vue'
import { RouterLink } from 'vue-router'
import { plural } from '../../utils/format'
import AdPreview from './AdPreview.vue'

const props = defineProps({
  build: { type: Object, required: true },
  site: { type: String, default: '' },
  wsId: { type: [Number, String], required: true },
  // На узком экране панель выезжает поверх чата.
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'jump'])

const KEYWORDS_SHOWN = 8
const STATUS_TEXT = {
  done: 'согласовано',
  pending: 'ждёт решения',
  todo: 'впереди',
  skipped: 'пропущено',
  rejected: 'на доработке',
}
const DRAFT_TEXT = { pending: 'ждёт решения', approved: 'создаётся', done: 'создан', error: 'ошибка' }

const expanded = reactive({})

const keywordCount = computed(() => props.build.groups.reduce((n, g) => n + g.keywords.length, 0))

const steps = computed(() => {
  const out = props.build.stages.map((s) => ({ ...s, meta: stageMeta(s) }))
  const draft = props.build.draft
  const status = !draft ? 'todo' : draft.status === 'done' ? 'done' : draft.status === 'error' ? 'rejected' : 'pending'
  out.push({
    key: 'draft',
    label: 'Черновик в Директе',
    status,
    action_id: draft?.action_id || null,
    meta: draft ? DRAFT_TEXT[draft.status] || draft.status : 'впереди',
  })
  return out
})
const doneCount = computed(() => steps.value.filter((s) => s.status === 'done').length)

const hasPending = computed(() => {
  const b = props.build
  const lists = [b.goals, b.directions, b.negatives, b.extras, b.settings, ...b.groups.flatMap((g) => [g.keywords, g.titles, g.texts])]
  return lists.some((list) => list.some((r) => r.pending))
})

const factBlocks = computed(() =>
  [
    { title: 'Ссылка и расширения', rows: props.build.extras },
    { title: 'Настройки', rows: props.build.settings },
  ].filter((b) => b.rows.length),
)

function stageMeta(stage) {
  if (stage.status === 'todo' || stage.status === 'skipped' || !stage.count) return STATUS_TEXT[stage.status] || ''
  if (stage.key === 'keywords') {
    const groups = props.build.groups.filter((g) => g.keywords.length).length
    return `${groups} ${plural(groups, 'группа', 'группы', 'групп')} · ${keywordCount.value} ${plural(keywordCount.value, 'фраза', 'фразы', 'фраз')}${stage.status === 'pending' ? ' · ждёт' : ''}`
  }
  if (stage.key === 'ads') {
    const titles = props.build.groups.reduce((n, g) => n + g.titles.length, 0)
    const texts = props.build.groups.reduce((n, g) => n + g.texts.length, 0)
    return `${titles} ${plural(titles, 'заголовок', 'заголовка', 'заголовков')} · ${texts} ${plural(texts, 'текст', 'текста', 'текстов')}${stage.status === 'pending' ? ' · ждёт' : ''}`
  }
  if (stage.key === 'negatives') {
    return `${stage.count} ${plural(stage.count, 'фраза', 'фразы', 'фраз')} · ${STATUS_TEXT[stage.status]}`
  }
  return `${stage.count} ${plural(stage.count, 'строка', 'строки', 'строк')} · ${STATUS_TEXT[stage.status]}`
}

function shown(group) {
  return expanded[group.name] ? group.keywords : group.keywords.slice(0, KEYWORDS_SHOWN)
}

function toggle(name) {
  expanded[name] = !expanded[name]
}
</script>
