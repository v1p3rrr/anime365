import { Alert, Modal, Select, Stack, Text } from '@mantine/core'
import { useEffect, useState } from 'react'

import { type LocalVideo } from '../api/title'
import { useT } from '../i18n'
import { useFormat } from '../i18n/format'
import { langCode } from '../i18n/flags'

interface Props {
  opened: boolean
  onClose: () => void
  /** Скачанные файлы этой серии. Их бывает несколько: озвучка и оригинал. */
  videos: LocalVideo[]
  episodeLabel: string
}

/**
 * Просмотр того, что уже скачано, — прямо в браузере.
 *
 * Плеер здесь обычный `<video controls>`, а не Artplayer, как для стрима.
 * Разница осознанная: у стрима нужны субтитры из отдельного файла, выбор
 * качества и переключение дорожек — там без обвязки не обойтись. Локальный
 * файл уже содержит всё внутри себя, и браузерный плеер с ним справляется сам.
 * Тянуть ради этого вторую копию тяжёлой библиотеки незачем.
 *
 * ⚠️ Браузер играет далеко не всё, что лежит в библиотеке. `.mkv` с H.264 и
 * AAC обычно идёт, а с HEVC или FLAC внутри — уже нет, и зависит это от
 * браузера, а не от нас. Перекодировать на лету мы не станем: на слабом
 * домашнем сервере это съело бы процессор ради одной вкладки. Поэтому при
 * ошибке честно говорим, что формат не поддерживается, и не делаем вид, что
 * дело в файле.
 */
export function LocalPlayerModal({ opened, onClose, videos, episodeLabel }: Props) {
  const { t } = useT()
  const format = useFormat()
  const [fileId, setFileId] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  // Сбрасываем выбор при открытии: серия могла смениться, а файл остаться в
  // состоянии от прошлой.
  useEffect(() => {
    if (opened) {
      setFileId(videos.length > 0 ? String(videos[0].file_id) : null)
      setFailed(false)
    }
  }, [opened, videos])

  const options = videos.map((video) => ({
    value: String(video.file_id),
    label:
      (video.langs.length > 0 ? `${video.langs.map(langCode).join(', ')} · ` : '') +
      video.filename +
      (video.size_bytes ? ` · ${format.size(video.size_bytes)}` : ''),
  }))

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={t('episode.playLocalTitle', { episode: episodeLabel })}
      size="xl"
      centered
    >
      <Stack gap="sm">
        {/* Выбор показываем только когда есть из чего выбирать. */}
        {options.length > 1 && (
          <Select
            data={options}
            value={fileId}
            onChange={(value) => {
              setFileId(value)
              setFailed(false)
            }}
            label={t('episode.playLocalFile')}
            allowDeselect={false}
          />
        )}

        {failed && (
          <Alert color="yellow">
            <Text size="sm">{t('episode.playLocalUnsupported')}</Text>
          </Alert>
        )}

        {fileId && (
          <video
            key={fileId}
            controls
            autoPlay
            src={`/api/stream/local/${fileId}`}
            onError={() => setFailed(true)}
            style={{ width: '100%', maxHeight: '70vh', background: '#000' }}
          />
        )}
      </Stack>
    </Modal>
  )
}
