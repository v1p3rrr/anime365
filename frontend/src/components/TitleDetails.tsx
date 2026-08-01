import { Badge, Group, Text, Tooltip } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'

import { titleApi } from '../api/title'
import { useT } from '../i18n'
import classes from './TitleDetails.module.css'

interface Props {
  seriesId: number
  /** Сколько серий у тайтла на Anime365 — чтобы заметить расхождение с фактом. */
  availableEpisodes: number
}

/** Дата вида `2027-01-01` в местном формате. Без времени: его источник не даёт. */
function useDate() {
  const { locale } = useT()
  return (iso: string | null | undefined) => {
    if (!iso) return null
    const parsed = new Date(iso)
    if (Number.isNaN(parsed.getTime())) return null
    return parsed.toLocaleDateString(locale, { day: 'numeric', month: 'long', year: 'numeric' })
  }
}

/**
 * Студия, состояние выхода и ход онгоинга — данные с Шикимори.
 *
 * Anime365 ничего этого не отдаёт: в каталоге есть `isAiring`, но он не
 * отличает «ещё не вышло» от «давно закончилось», а `numberOfEpisodes` — это
 * план, а не факт.
 *
 * Блок молча исчезает, если данных нет: у тайтла может не быть `mal_id`, а
 * Шикимори может быть недоступен. Ради студии страницу тайтла не ломаем.
 */
export function TitleDetails({ seriesId, availableEpisodes }: Props) {
  const { t } = useT()
  const formatDate = useDate()

  const query = useQuery({
    queryKey: ['shikimori-details', seriesId],
    queryFn: () => titleApi.details(seriesId),
    staleTime: 60 * 60 * 1000,
    retry: false,
  })

  const data = query.data
  if (!data?.available) return null

  const studios = data.studios ?? []
  const aired = data.episodes_aired ?? null
  // Расхождение важно назвать вслух: вышло 12, а скачать можно 9 — это разные
  // числа, и молча показывать одно из них значит вводить в заблуждение.
  const gap = aired !== null && aired > availableEpisodes

  return (
    <Group gap="xs" wrap="wrap">
      {data.status === 'anons' && (
        <Badge color="violet" variant="light">
          {t('catalog.cardAnnounced')}
        </Badge>
      )}

      {/* Логотип отдельным элементом, а не внутри чипа.
          Чип рассчитан на текст: картинка внутри растягивает его по высоте и
          сбивает выравнивание всего ряда бейджей. Здесь у логотипа
          фиксированная высота, ширина подстраивается — логотипы студий бывают
          и квадратные, и вытянутые, поэтому ограничивать надо именно высоту. */}
      {studios.map((studio) => (
        <Tooltip key={studio.id} label={t('title.studio', { name: studio.name })}>
          <Group gap={6} wrap="nowrap" className={classes.studio}>
            {studio.image && (
              <img
                src={studio.image}
                alt=""
                loading="lazy"
                className={classes.studioLogo}
                // Логотип может не отдаться: кэш деталей живёт сутки, и после
                // правки формата ссылки старые записи ещё какое-то время ведут
                // в никуда. Значок сломанной картинки хуже, чем просто
                // название студии, поэтому убираем элемент.
                onError={(event) => {
                  event.currentTarget.style.display = 'none'
                }}
              />
            )}
            <Text size="xs" fw={500}>
              {studio.name}
            </Text>
          </Group>
        </Tooltip>
      ))}

      {data.next_episode_at && (
        <Badge color="blue" variant="light">
          {t('title.nextEpisode', { date: formatDate(data.next_episode_at) ?? '' })}
        </Badge>
      )}

      {aired !== null && aired > 0 && (
        <Badge color={gap ? 'orange' : 'gray'} variant="light">
          {gap
            ? t('title.airedWithGap', { aired, available: availableEpisodes })
            : t('title.aired', { aired })}
        </Badge>
      )}

      {data.status === 'anons' && data.aired_on && (
        <Text size="xs" c="dimmed">
          {t('title.airsFrom', { date: formatDate(data.aired_on) ?? '' })}
        </Text>
      )}
    </Group>
  )
}
