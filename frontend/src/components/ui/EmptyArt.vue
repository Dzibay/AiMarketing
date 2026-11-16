<template>
  <svg class="eart" viewBox="0 0 160 112" aria-hidden="true" focusable="false">
    <ellipse class="eart__bg" cx="80" cy="62" rx="64" ry="44" />

    <!-- Нет проектов: папка и «плюс» -->
    <g v-if="name === 'projects'">
      <path class="l" d="M38 42a4 4 0 0 1 4-4h22l8 8h50a4 4 0 0 1 4 4v40a4 4 0 0 1-4 4H42a4 4 0 0 1-4-4Z" />
      <path class="m" d="M38 58h88" />
      <circle class="a" cx="120" cy="36" r="13" />
      <path class="k" d="M120 30v12M114 36h12" />
    </g>

    <!-- Нет кампаний: рупор -->
    <g v-else-if="name === 'campaigns'">
      <path class="l" d="M56 72l6 18h8l-4-16" />
      <path class="l" d="M50 48l42-16v56L50 72Z" />
      <path class="l" d="M42 48h8v24h-8a4 4 0 0 1-4-4V52a4 4 0 0 1 4-4Z" />
      <path class="l f" d="M102 50q6 10 0 20" />
      <path class="m f" d="M111 43q11 17 0 34" />
      <path class="a" d="M124 18q2 9 11 11q-9 2-11 11q-2-9-11-11q9-2 11-11Z" />
    </g>

    <!-- Новый чат: вопрос и ответ -->
    <g v-else-if="name === 'chat'">
      <path class="l" d="M36 32h56a8 8 0 0 1 8 8v22a8 8 0 0 1-8 8H56l-12 10V70h-8a8 8 0 0 1-8-8V40a8 8 0 0 1 8-8Z" />
      <path class="m" d="M44 46h40M44 56h26" />
      <path class="a" d="M90 60h34a7 7 0 0 1 7 7v12a7 7 0 0 1-7 7h-4v9l-11-9H90a7 7 0 0 1-7-7V67a7 7 0 0 1 7-7Z" />
      <circle class="d" cx="99" cy="73" r="2.2" />
      <circle class="d" cx="107" cy="73" r="2.2" />
      <circle class="d" cx="115" cy="73" r="2.2" />
    </g>

    <!-- Решений нет: планшет с галочкой -->
    <g v-else-if="name === 'done'">
      <rect class="l" x="46" y="28" width="60" height="72" rx="6" />
      <rect class="l" x="62" y="22" width="28" height="12" rx="4" />
      <path class="m" d="M58 50h30M58 62h36M58 74h22" />
      <circle class="a" cx="108" cy="84" r="14" />
      <path class="k" d="M101 84l5 5l9-10" />
    </g>

    <!-- Сигналов нет: ровная линия и галочка -->
    <g v-else-if="name === 'calm'">
      <rect class="l" x="32" y="34" width="94" height="58" rx="8" />
      <path class="m" d="M42 80h74" />
      <path class="l f" d="M42 64h24l5-9l7 17l5-8h33" />
      <circle class="a" cx="124" cy="36" r="13" />
      <path class="k" d="M117 36l5 5l9-10" />
    </g>

    <!-- История пуста: часы и стрелка назад -->
    <g v-else-if="name === 'history'">
      <circle class="l" cx="76" cy="62" r="32" />
      <path class="m" d="M76 36v4M76 84v4M50 62h4M98 62h4" />
      <path class="l f" d="M76 46v16l11 8" />
      <circle class="a" cx="114" cy="34" r="13" />
      <path class="k f" d="M108.5 37a6.5 6.5 0 1 0 1.6-7.6M109 25v5h5" />
    </g>

    <!-- Запросы не загружены: список и стрелка загрузки -->
    <g v-else-if="name === 'queries'">
      <rect class="l" x="40" y="24" width="64" height="78" rx="6" />
      <path class="m" d="M52 42h40M52 54h30M52 66h40M52 78h22" />
      <circle class="a" cx="108" cy="82" r="14" />
      <path class="k" d="M108 74v14M102 82.5l6 6l6-6" />
    </g>

    <!-- В отчёте нет данных: пустые столбцы -->
    <g v-else-if="name === 'report'">
      <path class="m eart__dash" d="M50 70h12v22H50ZM72 56h12v36H72ZM94 64h12v28H94Z" />
      <path class="l f" d="M38 28v64h88" />
      <circle class="a" cx="116" cy="38" r="12" />
      <path class="k" d="M110.5 38h11" />
    </g>

    <!-- Уведомлений нет: колокольчик спит -->
    <g v-else-if="name === 'bell'">
      <path class="l f" d="M71 84a9 9 0 0 0 18 0" />
      <path class="l" d="M80 28a22 22 0 0 0-22 22v16l-8 10h60l-8-10V50a22 22 0 0 0-22-22Z" />
      <circle class="a" cx="108" cy="34" r="11" />
      <path class="k f" d="M104 30h8l-8 8h8" />
    </g>

    <!-- Поиск ничего не нашёл: лупа и крестик -->
    <g v-else>
      <path class="l eart__handle" d="M90 74l18 18" />
      <circle class="l" cx="72" cy="56" r="25" />
      <path class="m" d="M60 50h24M60 60h16" />
      <circle class="a" cx="118" cy="34" r="12" />
      <path class="k" d="M113.5 29.5l9 9M122.5 29.5l-9 9" />
    </g>
  </svg>
</template>

<script setup>
/**
 * Рисунки пустых экранов: одна линия, монохром и лаймовый акцент. Цвета — переменные темы, поэтому рисунок
 * сам подстраивается под светлую и тёмную тему. name: projects, campaigns, chat, done, calm, history, queries,
 * report, bell, search.
 */
defineProps({ name: { type: String, default: 'search' } })
</script>

<style scoped>
.eart {
  display: block;
  width: var(--eart-w, 140px);
  height: auto;
  overflow: visible;
}
.eart__bg {
  fill: var(--surface-2);
}
.l,
.m,
.k {
  stroke-linecap: round;
  stroke-linejoin: round;
}
.l {
  fill: var(--surface);
  stroke: var(--ink-2);
  stroke-width: 2;
}
.m {
  fill: none;
  stroke: var(--line-2);
  stroke-width: 2;
}
.f {
  fill: none;
}
.a {
  fill: var(--lime);
  stroke: var(--ink-2);
  stroke-width: 2;
}
.k {
  fill: none;
  stroke: #141413;
  stroke-width: 2.2;
}
.d {
  fill: #141413;
}
.eart__dash {
  stroke-dasharray: 4 4;
}
.eart__handle {
  stroke-width: 7;
}
</style>
