/* Собирает ../index.html — один самодостаточный файл со всеми макетами: node docs/mockups/src/build.mjs */
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const dir = dirname(fileURLToPath(import.meta.url))
const read = (f) => readFileSync(join(dir, f), 'utf8')
const variants = ['gallery', 'v1', 'v2', 'v3']

/* Стили варианта вкладываются в .vN, скрипт — в свою функцию: варианты не мешают друг другу. */
const css = read('shell.css') + variants.map((v) => `\n.${v} {\n${read(`${v}.css`)}\n}\n`).join('')
const js = read('data.js') + read('runtime.js') + variants.map((v) => `\nVARIANTS.${v} = (function () {\n${read(`${v}.js`)}\n})()\n`).join('') + '\nboot()\n'

const html = `<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Макеты кабинета AiMarketing</title>
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
writeFileSync(join(dir, '..', 'index.html'), html)
console.log(`index.html: ${Math.round(html.length / 1024)} КБ`)
