import { Text } from '@mantine/core'
import { useState } from 'react'

/**
 * Логотип в шапке.
 *
 * Файл лежит в `public/`, а не в `src/assets/`: импорт из assets Vite
 * резолвит при сборке, и без файла проект просто не собрался бы. Логотип —
 * не та вещь, ради которой стоит ломать сборку, поэтому он подключается
 * обычным путём, а если файла нет — показывается текстовое название.
 *
 * Кладётся скриптом `scripts/prepare_logo.py`: он убирает белый фон и
 * обрезает поля.
 */

const LOGO_URL = '/logo.png'

interface Props {
  /** Высота в пикселях. Ширина считается сама по пропорции. */
  height?: number
}

export function Logo({ height = 28 }: Props) {
  const [failed, setFailed] = useState(false)

  if (failed) {
    return (
      <Text fw={600} size="sm" truncate>
        Anime365 Downloader
      </Text>
    )
  }

  return (
    <img
      src={LOGO_URL}
      alt="Anime365 Downloader"
      onError={() => setFailed(true)}
      style={{
        height,
        width: 'auto',
        // Логотип не должен растягивать шапку на узком экране.
        maxWidth: '52vw',
        objectFit: 'contain',
        display: 'block',
      }}
    />
  )
}
