import { Box, Image } from '@mantine/core'

interface Props {
  url: string | null
  /** Высота в пикселях. Ширина считается по пропорции постера 2:3. */
  height?: number
}

/**
 * Маленький постер в таблицах библиотеки.
 *
 * Зачем он там: по картинке ошибочная привязка видна мгновенно, а по названию
 * её надо прочитать и сопоставить. Именно так были замечены почти все промахи
 * маппинга — «Look Back» с постером «Kekkai Sensen» не спутаешь.
 *
 * Место занимает даже без картинки: иначе строки таблицы прыгали бы по ширине
 * в зависимости от того, у какого тайтла постер загрузился.
 */
export function PosterThumb({ url, height = 45 }: Props) {
  const width = Math.round((height * 2) / 3)

  if (!url) {
    return <Box w={width} h={height} style={{ flexShrink: 0 }} />
  }

  return (
    <Image
      src={url}
      w={width}
      h={height}
      radius={3}
      fit="cover"
      // Постеры приходят с Anime365, и часть ссылок битая. Пустое место лучше
      // сломанной картинки: таблица не должна разъезжаться из-за чужого 404.
      fallbackSrc={undefined}
      style={{ flexShrink: 0 }}
    />
  )
}
