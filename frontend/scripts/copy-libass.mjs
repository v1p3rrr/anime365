/**
 * Кладёт файлы libass-wasm в `public/libass/` перед сборкой.
 *
 * Зачем не импортом, как обычно. SubtitlesOctopus (движок внутри
 * artplayer-plugin-libass) запускает воркер и грузит `.wasm` **по пути
 * относительно самого воркера**. Vite при сборке переименовывает ассеты в
 * хешированные имена и раскладывает их плоско — и воркер, оказавшись
 * `/assets/subtitles-octopus-worker-XXXX.js`, ищет рядом
 * `/assets/subtitles-octopus-worker.wasm`, которого там нет.
 *
 * Ровно на этом мы уже обожглись с JASSUB: всё отдавалось с кодом 200, воркер
 * стартовал, а инициализация молча не завершалась — и ни ошибки, ни строчки в
 * консоли.
 *
 * `public/` Vite копирует как есть, без переименования, поэтому `.wasm`
 * гарантированно лежит рядом с воркером. Каталог в `.gitignore`: это копия
 * содержимого node_modules, и держать её в репозитории незачем.
 */

import { cp, mkdir, readdir } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const from = join(here, '..', 'node_modules', 'libass-wasm', 'dist', 'js')
const to = join(here, '..', 'public', 'libass')

await mkdir(to, { recursive: true })
await cp(from, to, { recursive: true })

const copied = await readdir(to)
console.log(`libass: скопировано файлов ${copied.length} -> public/libass`)
