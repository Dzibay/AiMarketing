/* Собирает ../../dialog.html — полный макет варианта 2 «Диалог» одним файлом: node docs/mockups/src/dialog/build.mjs */
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const dir = dirname(fileURLToPath(import.meta.url))
const read = (f) => readFileSync(join(dir, f), 'utf8')

/* Полоса над макетом: в неё добавились режим и ссылка на первые варианты. */
const bar = `
#bar { overflow-x: auto; scrollbar-width: none; }
#bar strong, #bar nav { flex: none; }
#bar nav { align-items: center; }
#bar em { margin-right: 4px; font-style: normal; }
#bar span { min-width: 0; }
#bar a { color: #c9ccd1; }
`
/* Стили первого варианта 2 берутся как есть, новые ложатся поверх; всё действует только внутри .v2. */
const css = read('../shell.css') + bar + `\n.v2 {\n${read('../v2.css')}\n${read('dialog.css')}\n}\n`
/* Все файлы живут в одной области видимости: core.js идёт раньше экранов, которые дополняют ACT и MODALS. */
const parts = ['../data.js', 'data.js', 'core.js', 'reports.js', 'home.js', 'chat.js', 'camps.js', 'queries.js', 'auto.js', 'history.js', 'settings.js', 'map.js']
const js = parts.map(read).join('\n') + '\nboot()\n'

const html = `<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Макет AiMarketing «Диалог»</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Manrope:wght@400;500;600;700;800&family=Onest:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
${css}</style>
</head>
<body>
<div id="bar"></div>
<div id="app"></div>
<script>
${js}</script>
</body>
</html>
`
writeFileSync(join(dir, '..', '..', 'dialog.html'), html)
console.log(`dialog.html: ${Math.round(html.length / 1024)} КБ`)
