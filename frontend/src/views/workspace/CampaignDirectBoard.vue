<template>
  <section class="board panel board--direct" aria-label="Доска кампании">
    <header class="board__head">
      <div class="board__title">
        <h3>{{ data?.campaign?.name || 'Кампания' }}</h3>
        <p class="hint">
          Как сейчас в Директе<template v-if="data"> · {{ summary }}</template> ·
          <button class="ai-check__link" type="button" :disabled="loading" @click="load">обновить</button>
        </p>
      </div>
    </header>

    <UiSkeleton v-if="loading && !data" :count="4" />
    <p v-else-if="error" class="hint tone-bad">{{ error }}</p>
    <template v-else-if="data">
      <p v-if="!data.groups.length" class="hint">В кампании пока нет групп.</p>
      <article v-for="g in data.groups" :key="g.adgroup_row_id || g.name" class="board-group">
        <header class="board-group__head">
          <strong>{{ g.name }}</strong>
          <small>{{ g.keywords.length }} {{ plural(g.keywords.length, 'фраза', 'фразы', 'фраз') }}</small>
        </header>
        <ul v-if="g.keywords.length" class="board-chips">
          <li v-for="k in shown(g)" :key="k.text">
            <span class="board-chips__direct" aria-hidden="true">✓</span>
            <span class="board-chips__text">{{ k.text }}</span>
          </li>
          <li v-if="g.keywords.length > KEYWORDS_SHOWN" class="board-chips__more">
            <button class="ai-check__link" type="button" @click="toggle(g)">
              {{ expanded.has(key(g)) ? 'свернуть' : `ещё ${g.keywords.length - KEYWORDS_SHOWN}` }}
            </button>
          </li>
        </ul>
        <div v-if="g.titles.length || g.texts.length" class="board-group__lines">
          <div v-for="part in lines(g)" :key="part.label" class="board-lines">
            <div class="board-lines__head">
              <span>{{ part.label }}</span>
              <small>{{ part.items.length }}</small>
            </div>
            <ul class="board-lines__list">
              <li v-for="t in part.items" :key="t.text" class="board-lines__row">
                <span class="board-lines__ok" aria-hidden="true">✓</span>
                <span class="board-lines__text">{{ t.text }}</span>
              </li>
            </ul>
          </div>
        </div>
      </article>
      <div v-if="data.negatives.length" class="board-group">
        <header class="board-group__head">
          <strong>Минус-фразы кампании</strong>
          <small>{{ data.negatives.length }}</small>
        </header>
        <ul class="board-chips">
          <li v-for="n in data.negatives" :key="n.text">
            <span class="board-chips__text">−{{ n.text }}</span>
          </li>
        </ul>
      </div>
    </template>
  </section>
</template>

<script setup>
/**
 * Доска уже созданной кампании (docs/board-plan.md, пункт 2): тот же вид, что у сборки в чате, но только то,
 * что есть в Директе, и без шагов. Правки — в разделе «Группы, объявления, фразы» или через ИИ в чате.
 */
import { computed, reactive, ref, watch } from 'vue'
import { aiApi, errorText } from '../../api/web'
import UiSkeleton from '../../components/ui/UiSkeleton.vue'
import { plural } from '../../utils/format'

const props = defineProps({
  campaignId: { type: [Number, String], required: true },
})

const KEYWORDS_SHOWN = 40
const data = ref(null)
const loading = ref(false)
const error = ref('')
const expanded = reactive(new Set())

const summary = computed(() => {
  const groups = data.value.groups
  const keywords = groups.reduce((n, g) => n + g.keywords.length, 0)
  return [
    `${groups.length} ${plural(groups.length, 'группа', 'группы', 'групп')}`,
    `${keywords} ${plural(keywords, 'фраза', 'фразы', 'фраз')}`,
  ].join(' · ')
})

const key = (g) => g.adgroup_row_id || g.name
const shown = (g) => (expanded.has(key(g)) ? g.keywords : g.keywords.slice(0, KEYWORDS_SHOWN))
const toggle = (g) => (expanded.has(key(g)) ? expanded.delete(key(g)) : expanded.add(key(g)))
const lines = (g) =>
  [
    { label: 'Заголовки', items: g.titles },
    { label: 'Тексты', items: g.texts },
  ].filter((p) => p.items.length)

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await aiApi.campaignBoard(props.campaignId)
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}

watch(() => props.campaignId, load, { immediate: true })
</script>
