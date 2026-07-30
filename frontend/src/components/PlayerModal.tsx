import { Alert, Badge, Group, Loader, Modal, Select, SimpleGrid, Stack, Text } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { useEffect, useRef, useState } from 'react'

import { LANG_COLORS, type EpisodeTranslation } from '../api/title'

interface Props {
  opened: boolean
  onClose: () => void
  /** Переводы этой серии — из них выбирается дорожка для просмотра. */
  translations: EpisodeTranslation[]
  episodeLabel: string
  seriesUrl: string | null
}

/**
 * Встроенный плеер.
 *
 * Видео идёт через наш прокси: прямые ссылки CDN содержат бессрочный
 * премиум-токен, и отдавать их в браузер нельзя.
 *
 * Субтитры ASS отрисовывает libass, собранный в WebAssembly (JASSUB). Нативный
 * `<track>` понимает только VTT, а тот не умеет ни позиционирования надписей,
 * ни караоке — для аниме это половина смысла субтитров. Модуль весит 4 МБ,
 * поэтому подгружается лениво: при открытии плеера, а не вместе со страницей.
 */
export function PlayerModal({ opened, onClose, translations, episodeLabel, seriesUrl }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const rendererRef = useRef<{ destroy: () => void } | null>(null)

  const playable = translations.filter((t) => t.kind !== 'sub')
  const [videoId, setVideoId] = useState<string | null>(null)
  const [subId, setSubId] = useState<string | null>(null)
  const [subsError, setSubsError] = useState<string | null>(null)
  const [subsLoading, setSubsLoading] = useState(false)

  const subtitleOptions = translations.filter((t) => t.kind === 'sub')

  // Выбор по умолчанию делаем при открытии, а не в useState: список переводов
  // приходит асинхронно вместе со страницей тайтла.
  useEffect(() => {
    if (!opened) return
    if (videoId === null && playable.length > 0) setVideoId(String(playable[0].id))
  }, [opened, playable, videoId])

  // Рендерер живёт ровно столько, сколько открыт плеер: он держит воркер и
  // канвас поверх видео, и брошенный экземпляр продолжал бы рисовать.
  useEffect(() => {
    let cancelled = false

    async function attach() {
      rendererRef.current?.destroy()
      rendererRef.current = null
      setSubsError(null)

      if (!opened || !subId || !videoRef.current) return

      setSubsLoading(true)
      try {
        const response = await fetch(`/api/stream/${subId}/subtitles`, {
          credentials: 'same-origin',
        })
        if (!response.ok) {
          const body = await response.json().catch(() => ({}))
          throw new Error(body.detail ?? `сервер ответил ${response.status}`)
        }
        const subContent = await response.text()
        if (cancelled || !videoRef.current) return

        // Пути к воркеру и wasm задаём явно: без них JASSUB ищет их рядом
        // с собой, а Vite раскладывает ассеты по хешированным именам.
        const { default: JASSUB } = await import('jassub')
        const workerUrl = (await import('jassub/dist/wasm/jassub-worker.js?url')).default
        const wasmUrl = (await import('jassub/dist/wasm/jassub-worker.wasm?url')).default

        if (cancelled || !videoRef.current) return
        rendererRef.current = new JASSUB({
          video: videoRef.current,
          subContent,
          workerUrl,
          wasmUrl,
        })
      } catch (error) {
        if (!cancelled) setSubsError((error as Error).message)
      } finally {
        if (!cancelled) setSubsLoading(false)
      }
    }

    void attach()
    return () => {
      cancelled = true
      rendererRef.current?.destroy()
      rendererRef.current = null
    }
  }, [opened, subId])

  const describe = (t: EpisodeTranslation) =>
    `${t.lang.toUpperCase()} · ${t.authors}${t.height ? ` · ${Math.min(t.height, 1080)}p` : ''}`

  return (
    <Modal opened={opened} onClose={onClose} title={`Просмотр — ${episodeLabel}`} size="xl">
      <Stack gap="sm">
        {playable.length === 0 ? (
          <Alert color="yellow" icon={<IconAlertTriangle size={18} />}>
            У этой серии нет переводов с видеодорожкой на выбранных языках.
          </Alert>
        ) : (
          <>
            <video
              ref={videoRef}
              key={videoId ?? 'none'}
              src={videoId ? `/api/stream/${videoId}/video` : undefined}
              controls
              autoPlay
              style={{ width: '100%', maxHeight: '60vh', background: '#000', borderRadius: 6 }}
            />

            {/* На 375px два селектора рядом сжимаются до «RU · StudioBand · 10»,
                и выбранное не прочитать. Раскладываем их в столбик. */}
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
              <Select
                size="xs"
                label="Дорожка"
                data={playable.map((t) => ({ value: String(t.id), label: describe(t) }))}
                value={videoId}
                onChange={setVideoId}
                allowDeselect={false}
              />
              <Select
                size="xs"
                label="Субтитры"
                placeholder="без субтитров"
                clearable
                data={subtitleOptions.map((t) => ({ value: String(t.id), label: describe(t) }))}
                value={subId}
                onChange={setSubId}
              />
            </SimpleGrid>

            {subsLoading && (
              <Group gap="xs">
                <Loader size="xs" />
                <Text size="xs" c="dimmed">
                  Загружаем субтитры и рендерер…
                </Text>
              </Group>
            )}

            {subsError && (
              <Alert color="red" p="xs">
                <Text size="xs">
                  Субтитры не подключились: {subsError}. Видео это не мешает — выберите
                  другой перевод или смотрите без них.
                </Text>
              </Alert>
            )}

            <Group gap="xs" wrap="wrap">
              <Badge size="xs" variant="light" color={LANG_COLORS[playable[0]?.lang] ?? 'gray'}>
                воспроизведение через сервис
              </Badge>
              <Text size="xs" c="dimmed">
                Если дорожка не играет — кодек может не поддерживаться браузером.
                {seriesUrl && ' Тогда откройте серию на сайте.'}
              </Text>
            </Group>
          </>
        )}
      </Stack>
    </Modal>
  )
}
