<template>
  <div ref="scroller" class="board panel" aria-label="Доска кампании">
    <header class="board__head">
      <div class="board__title">
        <h3>{{ build.campaign?.name || build.draft?.name || 'Новая кампания' }}</h3>
        <p class="hint">
          Шаг {{ currentIndex + 1 }} из {{ steps.length }} · {{ current.label }}
          <template v-if="direct?.moderated"> · отправлена на модерацию</template>
          <template v-else-if="locked"> · черновик {{ build.draft.status === 'done' ? 'создан' : 'создаётся' }}, правки закрыты</template>
        </p>
      </div>
      <div class="board__progress" aria-hidden="true">
        <span v-for="s in steps" :key="s.key" :class="`is-${s.status}`" />
      </div>
    </header>

    <!-- Черновик в Директе: что записано, что не прошло, «Повторить». -->
    <div v-if="direct" class="board-direct" :class="{ 'is-error': direct.error || direct.status === 'error' }">
      <p>
        <strong>{{ direct.moderated ? 'Кампания в Директе' : 'Черновик в Директе' }}</strong>
        <template v-if="writtenText"> · {{ writtenText }}</template>
        <RouterLink v-if="direct.campaign_row_id" class="ai-check__link" :to="`/w/${wsId}/campaigns/${direct.campaign_row_id}`">
          открыть
        </RouterLink>
      </p>
      <p class="hint">✓ у строки — она уже есть в Директе; черновик сам не запускается.</p>
      <p v-if="direct.error" class="tone-bad">Последняя запись не прошла: {{ direct.error }}</p>
      <template v-if="direct.warnings.length">
        <p class="tone-warn">Что не попало в Директ:</p>
        <ul>
          <li v-for="w in direct.warnings" :key="w">{{ w }}</li>
        </ul>
      </template>
      <div v-if="(direct.error || direct.warnings.length) && !direct.moderated" class="board-decision__buttons">
        <button class="btn btn--xs" type="button" :disabled="disabled" @click="emit('write')">Повторить запись</button>
      </div>
    </div>

    <!-- Сверка: кампанию правили в самом Директе. Конфликт с правкой доски — обе версии. -->
    <div v-if="drift.length && !direct?.moderated" class="board-sync is-drift" data-sync="drift">
      <p><strong>В Директе изменилось:</strong> {{ drift.length }} {{ plural(drift.length, 'правка', 'правки', 'правок') }}</p>
      <ul class="board-sync__list">
        <li v-for="(c, i) in drift" :key="`d${i}`" :class="{ 'is-conflict': c.conflict }">
          <span class="board-sync__op">{{ OPS[c.op] }}</span>
          <span>{{ changeText(c) }}</span>
          <template v-if="c.conflict">
            <small class="tone-warn">конфликт</small>
            <span class="board-sync__both">
              <span><b>в Директе:</b> {{ directVersion(c) }}</span>
              <span><b>на доске:</b> {{ boardVersion(c) }}</span>
            </span>
          </template>
        </li>
      </ul>
      <div class="board-decision__buttons">
        <button class="btn btn--xs btn--accent" type="button" :disabled="disabled" @click="emit('pull', false)">Подтянуть</button>
        <button v-if="drift.some((c) => c.conflict) || queue.length" class="btn btn--xs" type="button" :disabled="disabled" @click="emit('pull', true)">
          Оставить мои
        </button>
      </div>
      <p class="hint">«Подтянуть» — доска станет как в Директе; «Оставить мои» — по «Применить» Директ вернётся к доске.</p>
    </div>

    <!-- Очередь правок к записанным шагам: пишутся только по «Применить в Директ». -->
    <div v-if="queue.length && !direct?.moderated" class="board-sync" data-sync="queue">
      <p>
        <strong>Правки, которых нет в Директе:</strong> {{ queue.length }}
        <button class="ai-check__link" type="button" @click="queueOpen = !queueOpen">{{ queueOpen ? 'свернуть' : 'что изменится' }}</button>
      </p>
      <ul v-if="queueOpen" class="board-sync__list">
        <li v-for="(c, i) in queue" :key="`q${i}`">
          <span class="board-sync__op">{{ OPS[c.op] }}</span>
          <span>{{ changeText(c) }}</span>
        </li>
      </ul>
      <div class="board-decision__buttons">
        <button class="btn btn--xs btn--accent" type="button" :disabled="disabled" @click="emit('write')">
          Применить в Директ ({{ queue.length }} {{ plural(queue.length, 'изменение', 'изменения', 'изменений') }})
        </button>
        <button class="btn btn--xs btn--ghost" type="button" :disabled="disabled" @click="resetQueue">Сбросить</button>
      </div>
    </div>

    <!-- Мастер: открыт один шаг (текущий или прошлый, который открыли поправить), прошлые свёрнуты с ✓, будущие серые. -->
    <section
      v-for="(s, i) in steps"
      :key="s.key"
      class="wiz"
      :class="[`is-${s.status}`, { 'is-open': s.key === openKey, 'is-future': i > currentIndex }]"
      :data-step="s.key"
    >
      <button v-if="s.key !== openKey" type="button" class="wiz__row" :disabled="i > currentIndex" @click="open(s.key)">
        <span class="board__dot">{{ dotOf(s, i) }}</span>
        <strong>{{ s.label }}</strong>
        <small>{{ rowNote(s, i) }}</small>
      </button>
      <template v-else>
        <div class="wiz__title">
          <span class="board__dot">{{ dotOf(s, i) }}</span>
          <h4>{{ s.label }}</h4>
          <small v-if="s.optional" class="hint">можно пропустить</small>
          <button v-if="s.key !== current.key" class="ai-check__link" type="button" @click="open(current.key)">
            К текущему шагу
          </button>
        </div>

        <BoardSection v-if="s.key === 'brief'" name="goals" title="Бриф" bare :tables="tablesOf(s, 'goals')">
          <BoardFacts v-if="build.goals.length" :items="build.goals" />
          <p v-else class="hint">ИИ спросит про продукт, гео, бюджет и цель — бриф появится здесь.</p>
        </BoardSection>

        <BoardSection v-if="s.key === 'demand'" name="demand" title="Спрос" bare :tables="tablesOf(s, 'demand')">
          <BoardFacts v-if="build.directions.length" :items="build.directions" bars />
          <p v-else class="hint">Направления с частотностью из Вордстата — они станут группами.</p>
        </BoardSection>

        <BoardSection
          v-if="GROUP_STEPS.includes(s.key)"
          name="groups"
          title="Группы и фразы"
          bare
          :tables="tablesOf(s, 'groups')"
        >
          <template #tools>
            <small v-if="build.groups.length" class="board-section__meta">
              {{ build.groups.length }} {{ plural(build.groups.length, 'группа', 'группы', 'групп') }} · {{ keywordCount }}
              {{ plural(keywordCount, 'фраза', 'фразы', 'фраз') }}<template v-if="audienceCount">
                · {{ audienceCount }} {{ plural(audienceCount, 'аудитория', 'аудитории', 'аудиторий') }}</template><template v-if="imageCount">
                · {{ imageCount }} {{ plural(imageCount, 'картинка', 'картинки', 'картинок') }}</template>
            </small>
            <div v-if="s.key !== 'groups'" class="seg board__preview-mode" role="group" aria-label="Предпросмотр">
              <button
                v-for="m in PREVIEW_MODES"
                :key="m.key"
                type="button"
                :class="{ active: preview === m.key }"
                :aria-pressed="preview === m.key"
                @click="preview = m.key"
              >
                {{ m.label }}
              </button>
            </div>
          </template>
          <p v-if="!build.groups.length" class="hint">Группы с фразами появятся, когда ИИ подготовит этот шаг.</p>
          <BoardGroup
            v-for="g in build.groups"
            :key="g.name"
            :group="g"
            :names="groupNames"
            :site="site"
            :preview="preview"
            :sitelinks="build.sitelinks"
            :callouts="build.callouts"
            :ads-started="s.key !== 'groups' && adsStarted"
          />
        </BoardSection>

        <BoardSection v-if="s.key === 'groups'" name="negatives" title="Минус-фразы" :tables="tablesOf(s, 'negatives')">
          <ul v-if="build.negatives.length" class="board-chips">
            <li
              v-for="n in build.negatives"
              :key="n.text"
              :class="{ 'is-pending': n.pending, 'is-off': !isOn(n), 'is-own': n.own, 'is-direct-only': n.direct_only }"
            >
              <span v-if="n.in_direct && !n.pending" class="board-chips__direct" v-tip="n.direct_only ? 'Только в Директе — добавили там' : 'Есть в Директе'" aria-label="в Директе">✓</span>
              <UiCheckbox
                v-if="n.pending"
                :checked="isOn(n)"
                :disabled="disabled"
                :aria-label="n.text"
                @change="toggle(n)"
              />
              <span class="board-chips__text">−{{ n.text }}</span>
              <BoardRule :item="n" />
              <button
                class="board-x"
                type="button"
                v-tip="n.pending && !n.added ? 'Снять галочку' : 'Убрать'"
                :aria-label="`Убрать: ${n.text}`"
                :disabled="disabled"
                @click="remove('negative', '', [n])"
              >
                ×
              </button>
            </li>
          </ul>
          <p v-else class="hint">Минус-фразы ИИ предложит вместе с фразами или следом за ними.</p>
          <form v-if="build.groups.length" class="board-add" @submit.prevent="addNegative">
            <input
              v-model="negative"
              class="ai-input"
              maxlength="4096"
              placeholder="+ минус-фраза"
              aria-label="Новая минус-фраза"
              :disabled="disabled"
            />
            <button v-if="negative.trim()" class="btn btn--xs" type="submit" :disabled="disabled">Добавить</button>
          </form>
        </BoardSection>

        <BoardSection v-if="s.key === 'extras'" name="extras" title="Расширения" bare :tables="tablesOf(s, 'extras')">
          <BoardFacts v-if="build.extras.length" :items="build.extras" />
          <p v-if="!build.extras.length && !boardSitelinks.length && !boardCallouts.length" class="hint">
            Быстрые ссылки и уточнения: с ними объявление крупнее и заметнее в выдаче. Можно вписать свои — ИИ возьмёт
            их в кампанию.
          </p>
          <BoardExtras
            v-if="build.groups.length || boardSitelinks.length || boardCallouts.length"
            :sitelinks="boardSitelinks"
            :callouts="boardCallouts"
          />
        </BoardSection>

        <BoardSection v-if="s.key === 'settings'" name="settings" title="Настройки" bare :tables="tablesOf(s, 'settings')">
          <BoardFacts v-if="build.settings.length" :items="build.settings" />
          <p v-else class="hint">Стратегия, бюджет, гео и расписание.</p>
        </BoardSection>

        <BoardSection v-if="s.key === 'ready'" name="ready" title="Проверка" bare>
          <ul class="board-checks">
            <li v-for="c in build.checks" :key="c.key + c.label" :class="`is-${c.state}`">
              <span class="board-checks__icon" aria-hidden="true">{{ CHECK_ICONS[c.state] }}</span>
              <span>{{ c.label }}</span>
              <small v-if="c.note">{{ c.note }}</small>
              <!-- Группы спорят за запросы: ИИ подберёт минус-слова между ними и покажет правки на доске. -->
              <button
                v-if="c.ask && !locked"
                class="btn btn--xs board-checks__ask"
                type="button"
                v-tip="'ИИ подберёт минус-слова между группами и покажет правки здесь — на согласование'"
                :disabled="disabled"
                @click="emit('ask', c.ask)"
              >
                Развести группы
              </button>
            </li>
          </ul>

          <div v-if="build.campaign && direct?.moderated" class="board-draft is-done">
            <strong>Отправлено на модерацию</strong>
            <p class="hint">Показы начнутся, когда Директ примет кампанию — обычно в течение дня.</p>
            <RouterLink v-if="direct.campaign_row_id" class="btn btn--ghost" :to="`/w/${wsId}/campaigns/${direct.campaign_row_id}`">
              Открыть кампанию
            </RouterLink>
          </div>
          <div v-else-if="build.campaign && direct?.campaign_row_id && s.key === current.key" class="board-draft is-pending">
            <strong>Черновик «{{ build.campaign.name }}» заполнен</strong>
            <p v-if="!build.ready" class="hint tone-bad">
              В проверке есть красные пункты — поправьте их, прежде чем отправлять.
            </p>
            <div class="board-decision__buttons">
              <button
                class="btn btn--accent"
                type="button"
                :disabled="disabled || !build.ready || !!direct.error"
                @click="emit('moderate')"
              >
                Отправить на модерацию
              </button>
            </div>
            <p class="hint">Черновик сам не запустится: на модерацию его отправляете вы.</p>
          </div>
          <div v-else-if="build.draft" class="board-draft" :class="`is-${build.draft.status}`" :data-action="build.draft.action_id">
            <template v-if="build.draft.status === 'pending'">
              <strong>Черновик готов к созданию</strong>
              <p v-if="build.draft.summary" class="hint">{{ build.draft.summary }}</p>
              <p v-if="!build.ready" class="hint tone-bad">
                В проверке есть красные пункты — Директ может не принять кампанию. Поправьте на доске или напишите ИИ.
              </p>
              <div class="board-decision__buttons">
                <button class="btn btn--accent" type="button" :disabled="disabled" @click="emit('draft', true)">
                  Создать черновик в Директе
                </button>
                <button class="btn btn--ghost" type="button" :disabled="disabled" @click="emit('draft', false)">Отклонить</button>
              </div>
              <p class="hint">Черновик сам не запустится: на модерацию его отправляете вы.</p>
            </template>
            <template v-else-if="build.draft.status === 'done'">
              <strong>Черновик создан в Директе</strong>
              <template v-if="draftMissed.length">
                <p class="tone-warn">Что не попало в Директ:</p>
                <ul class="board-draft__missed">
                  <li v-for="w in draftMissed" :key="w">{{ w }}</li>
                </ul>
                <p class="hint">ИИ подготовит недостающее и пришлёт карточку на согласование — сам ничего не меняет.</p>
              </template>
              <p v-else-if="madeText" class="hint">Всё создано: {{ madeText }}.</p>
              <div class="board-decision__buttons">
                <button v-if="draftMissed.length" class="btn btn--accent" type="button" :disabled="disabled" @click="finishDraft">
                  Доделать с ИИ
                </button>
                <CampaignNextStep
                  v-if="build.draft.campaign_row_id"
                  :ws-id="Number(wsId)"
                  :campaign="{ campaign_row_id: build.draft.campaign_row_id, name: build.draft.name }"
                  :next="{ kind: 'moderate', label: 'Отправить на проверку' }"
                  :quiet="draftMissed.length > 0"
                />
                <RouterLink v-if="build.draft.campaign_row_id" class="btn btn--ghost" :to="`/w/${wsId}/campaigns/${build.draft.campaign_row_id}`">
                  Открыть кампанию
                </RouterLink>
              </div>
              <p class="hint">
                Показы начнутся, когда вы отправите черновик на проверку и Директ его примет — обычно в течение дня.
              </p>
            </template>
            <template v-else-if="build.draft.status === 'error'">
              <strong class="tone-bad">Черновик не создан</strong>
              <p v-if="build.draft.error" class="hint">{{ build.draft.error }}</p>
            </template>
            <strong v-else><span class="spinner" /> Черновик создаётся…</strong>
          </div>
          <p v-else-if="running" class="hint"><span class="spinner" /> ИИ готовит черновик…</p>
          <template v-else-if="s.key === current.key && !locked && !build.campaign">
            <p class="hint">Все шаги пройдены. ИИ соберёт черновик — создать его в Директе можно будет здесь.</p>
            <div class="board-decision__buttons">
              <button class="btn btn--accent" type="button" :disabled="disabled" @click="emit('ask', DRAFT_ASK)">
                Собрать черновик
              </button>
            </div>
          </template>
        </BoardSection>

        <!-- Решение по шагу: «Дальше» согласует отмеченное, ИИ готовит следующий шаг. -->
        <div v-if="!locked && stepTables(s).length" class="board-decision board-step__foot">
          <p v-for="c in stepChecks(s)" :key="c.key" class="hint" :class="BLOCKING.has(c.key) ? 'tone-bad' : 'tone-warn'">
            {{ c.label }}<template v-if="c.note">: {{ c.note }}</template>
          </p>
          <textarea
            v-model="comment"
            class="ai-input ai-input--area"
            rows="1"
            maxlength="2000"
            :disabled="disabled"
            placeholder="Что поправить — необязательно"
            aria-label="Комментарий к шагу"
          />
          <div class="board-decision__buttons">
            <button
              class="btn btn--accent"
              type="button"
              :disabled="disabled || !stepChosen(s) || stepBlocked(s)"
              v-tip="`Согласовать отмеченное (${stepChosen(s)}) — ИИ подготовит следующий шаг`"
              @click="next(s)"
            >
              {{ comment.trim() ? 'Дальше с правкой' : 'Дальше' }} →
            </button>
            <button
              class="btn btn--ghost"
              type="button"
              :disabled="disabled"
              v-tip="'ИИ покажет этот шаг заново с учётом комментария'"
              @click="redo(s)"
            >
              Переделать
            </button>
            <button v-if="stepSkippable(s)" class="btn btn--ghost" type="button" :disabled="disabled" @click="skip(s)">
              Пропустить
            </button>
          </div>
        </div>
        <div v-else-if="!locked && s.key !== 'ready' && s.key === current.key" class="board-step__wait">
          <p v-if="running" class="hint"><span class="spinner" /> ИИ готовит этот шаг…</p>
          <template v-else>
            <p class="hint">ИИ ещё не подготовил этот шаг.</p>
            <div class="board-decision__buttons">
              <button class="btn btn--accent" type="button" :disabled="disabled" @click="emit('ask', `Подготовь шаг «${s.label}».`)">
                Попросить ИИ
              </button>
              <button
                v-if="s.optional"
                class="btn btn--ghost"
                type="button"
                :disabled="disabled"
                @click="emit('ask', `Шаг «${s.label}» пропускаем — переходи к следующему.`)"
              >
                Пропустить
              </button>
            </div>
          </template>
        </div>
        <form
          v-else-if="!locked && s.key !== 'ready' && s.key !== current.key"
          class="board-add board-step__redo"
          @submit.prevent="askRedo(s)"
        >
          <input
            v-model="redoText"
            class="ai-input"
            maxlength="1000"
            placeholder="Что переделать на этом шаге — ИИ покажет изменения на согласование"
            aria-label="Что переделать на шаге"
            :disabled="disabled"
          />
          <button v-if="redoText.trim()" class="btn btn--xs" type="submit" :disabled="disabled">Попросить ИИ</button>
        </form>
      </template>
    </section>
  </div>
</template>

<script setup>
/**
 * Доска сборки новой кампании — мастер по шагам (docs/board-plan.md, пункт 3): Бриф → Спрос → Группы и фразы →
 * Объявления → Картинки → Расширения → Настройки → Проверка. Открыт один шаг: текущий (на нём таблица ИИ с
 * галочками и кнопка «Дальше») или прошлый, который открыли поправить; прошлые свёрнуты с ✓ и итогом, будущие
 * серые. Правки маркетолога (фразы, группы, заголовки, тексты, картинки, ссылки) уходят через edit(ops) и
 * возвращают чат целиком. Данные — build из get_chat.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, provide, reactive, ref, watch } from 'vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import { RouterLink } from 'vue-router'
import { aiApi } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { plural } from '../../utils/format'
import CampaignNextStep from '../../components/CampaignNextStep.vue'
import BoardExtras from './BoardExtras.vue'
import BoardFacts from './BoardFacts.vue'
import BoardGroup from './BoardGroup.vue'
import BoardRule from './BoardRule.vue'
import BoardSection from './BoardSection.vue'

const props = defineProps({
  build: { type: Object, required: true },
  // Действия чата: ждущие таблицы этапов — с их строками и пояснением.
  actions: { type: Array, default: () => [] },
  site: { type: String, default: '' },
  wsId: { type: [Number, String], required: true },
  disabled: { type: Boolean, default: false },
  // ИИ сейчас отвечает — шаг готовится.
  running: { type: Boolean, default: false },
  // async (ops) => true, если правка прошла.
  edit: { type: Function, required: true },
  // Генерация картинок подключена в настройках.
  imageGen: { type: Boolean, default: false },
})
// decide — решение по одной таблице (id, { approve, rows, comment, skip? }); board — решение по таблицам старой
// доски ({ approve, rows, comment, only }); draft — карточка создания черновика (approve); ask — вопрос ИИ в чат (text).
// write — «Применить в Директ» (очередь правок и незаписанное); reset — сбросить очередь правок;
// pull — плашка «В Директе изменилось» (keep: false — «Подтянуть», true — «Оставить мои»);
// moderate — отправить черновик на модерацию.
const emit = defineEmits(['decide', 'board', 'draft', 'ask', 'write', 'reset', 'pull', 'moderate'])

// --- Черновик в Директе (сборка по шагам) -------------------------------------------------------

const direct = computed(() => props.build.direct || null)
const writtenText = computed(() => {
  const w = direct.value?.written
  if (!w) return ''
  return [
    count(w.groups, 'группа', 'группы', 'групп'),
    count(w.keywords, 'фраза', 'фразы', 'фраз'),
    count(w.ads, 'объявление', 'объявления', 'объявлений'),
  ].join(' · ')
})

// --- Очередь правок и сверка с Директом (пункт 4) -------------------------------------------------

const queue = computed(() => props.build.queue || [])
const drift = computed(() => props.build.drift || [])
const queueOpen = ref(false)
const OPS = { add: '+', remove: '−', edit: '~' }
const KINDS = {
  campaign: 'кампания',
  group: 'группа',
  keyword: 'фраза',
  negative: 'минус-фраза',
  audience: 'аудитория',
  ad: 'объявление',
  title: 'заголовок',
  text: 'текст',
}

function changeText(c) {
  const kind = KINDS[c.kind]
  if (c.kind === 'group' && c.op === 'edit' && c.group && c.group !== c.text) return `группа «${c.group}» → «${c.text}»`
  const where = c.group && c.kind !== 'group' ? ` · ${c.group}` : ''
  return kind ? `${kind} «${c.text}»${where}` : `${c.text}${where}`
}

function directVersion(c) {
  if (c.direct) return [...(c.direct.titles || []), ...(c.direct.texts || [])].join(' · ') || '—'
  return c.op === 'remove' ? 'удалено' : c.text
}

// Своя правка к тому же объекту в очереди «Применить».
function boardVersion(c) {
  const norm = (t) => String(t || '').trim().toLowerCase()
  const same = queue.value.filter((q) => {
    if ((q.group || '') !== (c.group || '')) return false
    if (c.kind === 'keyword' || c.kind === 'negative') return q.kind === c.kind && norm(q.text) === norm(c.text)
    return c.kind === 'group' || ['ad', 'title', 'text'].includes(q.kind)
  })
  const edits = same.map((q) => `${OPS[q.op]} ${changeText(q)}`)
  if (c.kind === 'group' && c.op === 'edit') edits.unshift(`«${c.group}»`)
  return edits.length ? edits.join('; ') : 'своя правка'
}

async function resetQueue() {
  const ok = await askConfirm(`Сбросить ${queue.value.length} ${plural(queue.value.length, 'правку', 'правки', 'правок')}? Доска вернётся к тому, что в Директе.`, {
    title: 'Сбросить правки',
    confirmText: 'Сбросить',
  })
  if (ok) emit('reset')
}

// --- Что не попало в черновик -------------------------------------------------------------------

const draftMissed = computed(() => props.build.draft?.warnings || [])
const madeText = computed(() => {
  const made = props.build.draft?.made
  if (!made) return ''
  return [
    `${made.groups} ${plural(made.groups, 'группа', 'группы', 'групп')}`,
    `${made.keywords} ${plural(made.keywords, 'фраза', 'фразы', 'фраз')}`,
    `${made.ads} ${plural(made.ads, 'объявление', 'объявления', 'объявлений')}`,
  ].join(', ')
})

function finishDraft() {
  const draft = props.build.draft
  const name = draft.name ? ` «${draft.name}»` : ''
  const id = draft.campaign_row_id ? ` (campaign_row_id ${draft.campaign_row_id})` : ''
  const missed = draftMissed.value.map((w) => `— ${w}`).join('\n')
  emit(
    'ask',
    `Доделай черновик${name}${id}: при создании в Директ не попало:\n${missed}\n` +
      'Разберись, почему, и подготовь недостающее по согласованному на доске — пришли карточкой на согласование.',
  )
}

// --- Шаги мастера -------------------------------------------------------------------------------

// Этапы сборки (build.stages) → шаги мастера. Аудитории и минус-фразы — необязательные части «Групп».
const WIZARD = [
  { key: 'brief', label: 'Бриф', stages: ['goals'] },
  { key: 'demand', label: 'Спрос', stages: ['analysis'] },
  { key: 'groups', label: 'Группы и фразы', stages: ['keywords', 'negatives', 'audiences'], required: ['keywords'] },
  { key: 'ads', label: 'Объявления', stages: ['ads'] },
  { key: 'images', label: 'Картинки', stages: ['images'], optional: true },
  { key: 'extras', label: 'Расширения', stages: ['extras'], optional: true },
  { key: 'settings', label: 'Настройки', stages: ['settings'] },
  { key: 'ready', label: 'Проверка и черновик', stages: [] },
]
const OPTIONAL_STAGES = new Set(['negatives', 'audiences', 'images', 'extras'])
const GROUP_STEPS = ['groups', 'ads', 'images']
// Где на шаге лежит таблица этапа: у «Групп» минус-фразы — своим разделом.
const SECTION_OF = {
  goals: 'goals',
  analysis: 'demand',
  keywords: 'groups',
  negatives: 'negatives',
  audiences: 'groups',
  ads: 'groups',
  images: 'groups',
  extras: 'extras',
  settings: 'settings',
}
// Проверки «Готовности», которые относятся к шагу; BLOCKING — красные из них не дают нажать «Дальше»
// (их снимают галочками: больше 7 заголовков, лишние картинки, ссылки и уточнения).
const STEP_CHECKS = {
  groups: ['keywords'],
  ads: ['ads', 'limits', 'unique'],
  images: ['images'],
  extras: ['extras', 'link'],
}
const BLOCKING = new Set(['limits', 'images', 'extras'])
const STATUS = {
  done: 'согласовано',
  pending: 'ждёт «Дальше»',
  todo: 'впереди',
  skipped: 'пропущено',
  rejected: 'ИИ переделывает',
}
const DRAFT_TEXT = { pending: 'черновик ждёт решения', approved: 'черновик создаётся', done: 'черновик создан', error: 'ошибка' }
const DRAFT_ASK = 'Все шаги согласованы — собери черновик кампании из согласованного на доске.'
const CHECK_ICONS = { ok: '✓', warn: '!', bad: '×' }
const PREVIEW_MODES = [
  { key: 'desktop', label: 'Компьютер' },
  { key: 'mobile', label: 'Телефон' },
  { key: 'both', label: 'Оба' },
]
const PREVIEW_KEY = 'board-preview'

const scroller = ref(null)
const busy = ref(false)
const negative = ref('')
const comment = ref('')
const redoText = ref('')
const preview = ref(localStorage.getItem(PREVIEW_KEY) || 'both')
// { id ждущей таблицы: [номера строк без галочки] } — решение уходит с этими галочками.
const off = reactive({})

watch(preview, (mode) => localStorage.setItem(PREVIEW_KEY, mode))

// Черновик создаётся или создан — сервер правки не примет.
const locked = computed(() => ['approved', 'done'].includes(props.build.draft?.status) || !!props.build.direct?.moderated)
const disabled = computed(() => props.disabled || busy.value || locked.value)

const pendingTables = computed(() => {
  const ids = new Set(props.build.action_ids || [])
  return props.actions.filter((a) => a.tool === 'propose_items' && a.status === 'pending' && ids.has(a.id))
})

const stageLabels = computed(() => Object.fromEntries(props.build.stages.map((s) => [s.key, s.label])))
// Таблицы старой доски (одно согласование на всё): решаются через decide_build по одной.
const heldIds = computed(() => new Set(props.build.held || []))

function stepStatus(step, stages) {
  if (step.key === 'ready') {
    if (props.build.direct?.moderated) return 'done'
    const draft = props.build.draft
    if (!draft) return 'todo'
    return draft.status === 'done' ? 'done' : draft.status === 'error' ? 'rejected' : 'pending'
  }
  const mine = step.stages.map((k) => stages[k] || 'todo')
  if (mine.includes('pending')) return 'pending'
  if (mine.includes('rejected')) return 'rejected'
  const required = step.optional ? [] : step.required || step.stages
  const states = required.map((k) => stages[k] || 'todo')
  if (step.optional) {
    if (mine.every((st) => st === 'skipped')) return 'skipped'
    return mine.some((st) => st === 'done') ? 'done' : 'todo'
  }
  return states.every((st) => st === 'done' || st === 'skipped') ? 'done' : 'todo'
}

const steps = computed(() => {
  const stages = Object.fromEntries(props.build.stages.map((s) => [s.key, s.status]))
  return WIZARD.map((w) => ({ ...w, status: stepStatus(w, stages) }))
})

// Текущий шаг: где ждёт таблица, иначе первый непройденный; черновик есть — «Проверка».
const currentIndex = computed(() => {
  const list = steps.value
  const waiting = list.findIndex((s) => s.status === 'pending' && s.key !== 'ready')
  if (waiting >= 0) return waiting
  if (props.build.draft) return list.length - 1
  const next = list.findIndex((s) => s.status === 'todo' || s.status === 'rejected')
  return next >= 0 ? next : list.length - 1
})
const current = computed(() => steps.value[currentIndex.value])

// Открытый шаг: прошлый, который открыл маркетолог, — иначе текущий.
const opened = ref(null)
const openKey = computed(() => opened.value || current.value.key)

function open(key) {
  opened.value = key === current.value.key ? null : key
  nextTick(() => show(key))
}

watch(
  () => current.value.key,
  (key) => {
    opened.value = null
    comment.value = ''
    nextTick(() => show(key))
  },
)

function stepTables(step) {
  return pendingTables.value
    .filter((a) => step.stages.includes(a.args?.stage))
    .map((a) => ({ ...a, stageLabel: stageLabels.value[a.args?.stage] || a.args?.stage }))
}

function tablesOf(step, section) {
  return stepTables(step).filter((a) => SECTION_OF[a.args?.stage] === section)
}

function stepChosen(step) {
  return stepTables(step).reduce((n, t) => n + chosenOf(t).length, 0)
}

function stepChecks(step) {
  const keys = STEP_CHECKS[step.key] || []
  return (props.build.checks || []).filter((c) => keys.includes(c.key) && c.state === 'bad')
}

function stepBlocked(step) {
  return stepChecks(step).some((c) => BLOCKING.has(c.key))
}

function stepSkippable(step) {
  const list = stepTables(step)
  return list.length > 0 && list.every((t) => OPTIONAL_STAGES.has(t.args?.stage))
}

function dotOf(step, i) {
  if (step.status === 'done') return '✓'
  if (step.status === 'skipped') return '–'
  return i + 1
}

const groupNames = computed(() => props.build.groups.map((g) => g.name))
const keywordCount = computed(() => props.build.groups.reduce((n, g) => n + g.keywords.filter(isOn).length, 0))
const audienceCount = computed(() => props.build.groups.reduce((n, g) => n + (g.audiences || []).filter(isOn).length, 0))
const imageCount = computed(() => props.build.groups.reduce((n, g) => n + (g.images || []).filter(isOn).length, 0))
const adsStarted = computed(() => props.build.stages.some((s) => s.key === 'ads' && s.status !== 'todo'))

function count(n, one, few, many) {
  return `${n} ${plural(n, one, few, many)}`
}

function factsLine(items) {
  return items
    .filter(isOn)
    .slice(0, 2)
    .map((f) => f.value || f.text)
    .join(' · ')
}

/** Итог шага одной строкой — у свёрнутого прошлого шага. */
function summary(key) {
  const b = props.build
  const on = (list) => (list || []).filter(isOn).length
  const sum = (field) => b.groups.reduce((n, g) => n + on(g[field]), 0)
  switch (key) {
    case 'brief':
      return factsLine(b.goals)
    case 'demand':
      return count(on(b.directions), 'направление', 'направления', 'направлений')
    case 'groups':
      return [
        count(b.groups.length, 'группа', 'группы', 'групп'),
        count(keywordCount.value, 'фраза', 'фразы', 'фраз'),
        on(b.negatives) ? count(on(b.negatives), 'минус-фраза', 'минус-фразы', 'минус-фраз') : '',
      ].filter(Boolean).join(' · ')
    case 'ads':
      return `${count(sum('titles'), 'заголовок', 'заголовка', 'заголовков')} · ${count(sum('texts'), 'текст', 'текста', 'текстов')}`
    case 'images':
      return count(imageCount.value, 'картинка', 'картинки', 'картинок')
    case 'extras':
      return [
        count(on(boardSitelinks.value), 'быстрая ссылка', 'быстрые ссылки', 'быстрых ссылок'),
        count(on(boardCallouts.value), 'уточнение', 'уточнения', 'уточнений'),
      ].join(' · ')
    case 'settings':
      return factsLine(b.settings)
    default:
      return ''
  }
}

function rowNote(step, i) {
  if (step.key === 'ready' && props.build.direct?.moderated) return 'отправлено на модерацию'
  if (step.key === 'ready') return props.build.draft ? DRAFT_TEXT[props.build.draft.status] || '' : 'впереди'
  if (step.status === 'done') return summary(step.key) || STATUS.done
  if (i === currentIndex.value && step.status === 'todo') return props.running ? 'ИИ готовит…' : 'текущий шаг'
  return STATUS[step.status] || ''
}

// Быстрые ссылки и уточнения доски: из таблиц этапа, а нет их там — из карточки создания (без повторов по группам).
function boardLinks(key) {
  if (props.build[key]?.length) return props.build[key]
  const seen = new Set()
  const out = []
  for (const g of props.build.groups) {
    for (const item of g[key] || []) {
      const id = item.text.toLowerCase()
      if (seen.has(id)) continue
      seen.add(id)
      out.push(item)
    }
  }
  return out
}
const boardSitelinks = computed(() => boardLinks('sitelinks'))
const boardCallouts = computed(() => boardLinks('callouts'))

watch(
  pendingTables,
  (list) => {
    for (const a of list) {
      if (!(a.id in off)) off[a.id] = (a.args?.rows || []).filter((r) => r.checked === false).map((r) => r.n)
    }
  },
  { immediate: true },
)

onMounted(() => {
  if (currentIndex.value === 0) return
  show(current.value.key, false)
  // Шрифты догружаются и меняют высоту шагов — встать к шагу ещё раз.
  document.fonts?.ready.then(() => show(openKey.value, false))
})

function isOn(item) {
  if (!item.pending) return item.checked !== false
  const list = off[item.action_id]
  return list ? !list.includes(item.n) : item.checked !== false
}

function toggle(item) {
  setOn(item, !isOn(item))
}

function setOn(item, on) {
  const list = off[item.action_id] || []
  off[item.action_id] = on ? list.filter((n) => n !== item.n) : [...new Set([...list, item.n])]
  scheduleChecks()
}

function offOf(table) {
  return off[table.id] || []
}

function setOff(id, list) {
  off[id] = [...list]
  scheduleChecks()
}

// Галочки — и на сервер: пределы доски, «Готовность» и ИИ считают те же строки, что отмечены здесь.
// Правка уходит вместе с ними, одни галочки — чуть погодя, пачкой.
const CHECKS_DELAY = 600
let checksTimer = null
let checksRun = null

function checksOp() {
  return { op: 'checks', off: Object.fromEntries(pendingTables.value.map((t) => [t.id, off[t.id] || []])) }
}

function checksDirty() {
  return pendingTables.value.some((t) => {
    const list = off[t.id] || []
    return (t.args?.rows || []).some((r) => (r.checked === false) !== list.includes(r.n))
  })
}

function scheduleChecks() {
  clearTimeout(checksTimer)
  checksTimer = setTimeout(syncChecks, CHECKS_DELAY)
}

/** Отменить отложенную отправку галочек и дождаться начатой. */
async function settleChecks() {
  clearTimeout(checksTimer)
  checksTimer = null
  while (checksRun) await checksRun
}

async function syncChecks() {
  checksTimer = null
  // По одной отправке за раз: две сразу спорили бы за одну таблицу.
  while (checksRun) await checksRun
  // ИИ отвечает или идёт решение — отправим, когда доска освободится.
  if (props.disabled || busy.value || locked.value || !checksDirty()) return
  checksRun = props.edit([checksOp()]).finally(() => {
    checksRun = null
  })
  await checksRun
}

watch(
  () => props.disabled,
  (now) => !now && checksDirty() && scheduleChecks(),
)
onBeforeUnmount(() => clearTimeout(checksTimer))

async function edit(ops) {
  if (disabled.value) return false
  busy.value = true
  try {
    await settleChecks()
    return await props.edit([checksOp(), ...ops])
  } finally {
    busy.value = false
  }
}

/** Убрать с доски: предложенное ИИ в ждущей таблице — снять галочку, остальное — правкой. */
async function remove(kind, group, items) {
  const ops = []
  for (const item of items) {
    // Ссылка из общей строки «A, B» (shared): галочка сняла бы всю строку — убираем правкой.
    if (item.pending && !item.added && !item.shared) setOn(item, false)
    // Картинка — по хэшу; подпись — для заметки ИИ.
    else if (kind === 'image') ops.push({ op: 'remove', kind, group, text: item.hash, name: item.text })
    else ops.push({ op: 'remove', kind, group, text: item.text })
  }
  return ops.length ? edit(ops) : true
}

async function addNegative() {
  const text = negative.value.trim()
  if (text && (await edit([{ op: 'add', kind: 'negative', text }]))) negative.value = ''
}

// Решение несёт свои строки — галочки отдельно не отправляем, но и не шлём их наперегонки с решением.
async function decide(id, payload) {
  await settleChecks()
  emit('decide', id, payload)
}

/** Номера строк таблицы, оставленных с галочкой. */
function chosenOf(table) {
  const list = off[table.id] || []
  return (table.args?.rows || []).map((r) => r.n).filter((n) => !list.includes(n))
}

/**
 * Решение по шагу. Таблица мастера — одна на шаг (ИИ ждёт ответа); таблицы старой доски шага решаются вместе.
 * how: 'next' — согласовать отмеченное, 'redo' — переделать, 'skip' — пропустить необязательный этап.
 */
async function decideStep(step, how) {
  const list = stepTables(step)
  if (!list.length) return
  await settleChecks()
  const text = comment.value.trim()
  const held = list.filter((t) => heldIds.value.has(t.id))
  if (held.length) {
    const rows = Object.fromEntries(held.map((t) => [t.id, how === 'skip' ? [] : chosenOf(t)]))
    emit('board', { approve: how !== 'redo', rows, comment: text, only: held.map((t) => t.id) })
    return
  }
  const table = list[0]
  if (how === 'skip') emit('decide', table.id, { approve: true, skip: true, rows: [], comment: text })
  else if (how === 'redo') emit('decide', table.id, { approve: false, rows: [], comment: text })
  else emit('decide', table.id, { approve: true, rows: chosenOf(table), comment: text })
}

const next = (step) => decideStep(step, 'next')
const redo = (step) => decideStep(step, 'redo')
const skip = (step) => decideStep(step, 'skip')

/** Прошлый шаг: попросить ИИ переделать — он покажет изменения таблицей этого этапа. */
function askRedo(step) {
  const text = redoText.value.trim()
  if (!text) return
  emit('ask', `Переделай шаг «${step.label}»: ${text}`)
  redoText.value = ''
}

// Картинки: кабинет кампании (или выбранный, пока ИИ его не назвал), библиотека кампаний — один раз на доску.
const chosenAccount = ref(null)
let libraryLoad = null

function library() {
  libraryLoad ??= aiApi.wsImages(props.wsId).then(
    (r) => r.items || [],
    (err) => {
      libraryLoad = null
      throw err
    },
  )
  return libraryLoad
}

const images = {
  wsId: () => props.wsId,
  account: computed(() => props.build.account_id ?? chosenAccount.value),
  accounts: computed(() => props.build.accounts || []),
  chooseAccount: (id) => {
    chosenAccount.value = id
  },
  canGenerate: computed(() => props.imageGen),
  library,
}

provide('board', { isOn, toggle, remove, edit, disabled, offOf, setOff, decide, images })

/** Прокрутить доску к шагу и подсветить его. На узком экране доска не прокручивается сама — страницу. */
function show(key, smooth = true) {
  const box = scroller.value
  const el = key && box?.querySelector(`[data-step="${key}"]`)
  if (!el || !el.offsetParent) return
  const behavior = smooth ? 'smooth' : 'auto'
  if (box.scrollHeight > box.clientHeight + 1) {
    // Шапка доски закреплена сверху — шаг должен встать под неё.
    const head = box.querySelector('.board__head')?.offsetHeight || 0
    const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - head - 8
    box.scrollTo({ top: Math.max(0, top), behavior })
  } else {
    el.scrollIntoView({ behavior, block: 'start' })
  }
  if (!smooth) return
  el.classList.remove('is-flash')
  void el.offsetWidth
  el.classList.add('is-flash')
  setTimeout(() => el.classList.remove('is-flash'), 1600)
}

/** Шаг доски, где решается действие чата (таблица этапа или карточка создания). */
function showAction(actionId) {
  if (props.build.draft?.action_id === actionId) return open('ready')
  const action = props.actions.find((a) => a.id === actionId)
  const step = WIZARD.find((w) => w.stages.includes(action?.args?.stage))
  open(step ? step.key : current.value.key)
}

defineExpose({ show, showAction })
</script>

<style scoped>
.board-checks__ask {
  grid-column: 2;
  justify-self: start;
  margin-top: 0.2rem;
}
</style>
