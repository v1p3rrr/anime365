import { Button, Group, Tooltip } from '@mantine/core'
import { IconDeviceTv, IconExternalLink } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'

import { titleApi } from '../api/title'
import { CollectionButton } from './CollectionButton'
import { usePermissions } from '../hooks/usePermissions'
import { useT } from '../i18n'

interface Props {
  seriesId: number
  /** Подставляется в имя новой коллекции. */
  title?: string
  /** Страница тайтла на самом Anime365. Приходит в ответе API как `series.url`. */
  anime365Url: string | null
}

/**
 * Переходы «наружу» — на Anime365 и в Jellyfin.
 *
 * Кнопками, а не строчкой в общем списке ссылок. Список рядом (MAL, Shikimori,
 * AniDB и прочие) — это справочные материалы о тайтле; здесь же два места, где
 * с тайтлом можно что-то *сделать*: посмотреть его у себя или открыть исходную
 * страницу. Смешивать их со справочником значит прятать действие среди сносок.
 *
 * Ссылка в Jellyfin запрашивается отдельно и появляется, только когда нашлась.
 * Причин не найтись четыре, и каждая чинится по-своему, поэтому подсказка у
 * неактивной кнопки разная — «нет ссылки» без объяснения отправило бы
 * пользователя гадать между настройками, привязкой и сканированием.
 */
export function TitleQuickLinks({ seriesId, title, anime365Url }: Props) {
  const { t } = useT()
  const { can } = usePermissions()

  const jellyfin = useQuery({
    queryKey: ['jellyfin-link', seriesId],
    queryFn: () => titleApi.jellyfin(seriesId),
    // Интеграция выключена у большинства — незачем ходить за этим повторно
    // при каждом возврате на страницу.
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  const link = jellyfin.data
  // Прячем только при выключенном тумблере: там кнопка-заглушка была бы шумом,
  // пользователь интеграцию не подключал. А вот включённый тумблер без адреса
  // или ключа — уже осознанное действие, и отвечать на него пустым местом
  // нельзя: именно так выглядит «включил, и ничего не произошло».
  const showJellyfin = link != null && link.reason !== 'disabled'

  if (!anime365Url && !showJellyfin) return null

  return (
    <Group gap="xs">
      {anime365Url && (
        <Button
          component="a"
          href={anime365Url}
          target="_blank"
          rel="noreferrer noopener"
          variant="light"
          size="compact-sm"
          rightSection={<IconExternalLink size={13} />}
        >
          {t('title.openOnAnime365')}
        </Button>
      )}

      {/* Коллекции — рядом со ссылкой в Jellyfin: это тоже про него.
          Кнопку показываем ТОЛЬКО когда тайтл в Jellyfin действительно нашёлся.
          Пока ничего не скачано, папки у него там нет, добавлять в коллекцию
          нечего — и предлагать это значит вести к отказу «ни один тайтл не
          найден». Проверка есть и на сервере, здесь она просто убирает кнопку,
          которая всё равно ничего бы не сделала. */}
      {/* Коллекция меняет чужую библиотеку — право `library`. */}
      {link?.available && can('library') && (
        <CollectionButton seriesIds={[seriesId]} suggestedName={title ?? ''} />
      )}

      {showJellyfin &&
        (link.available && link.url ? (
          <Button
            component="a"
            href={link.url}
            target="_blank"
            rel="noreferrer noopener"
            variant="light"
            color="grape"
            size="compact-sm"
            leftSection={<IconDeviceTv size={14} />}
            rightSection={<IconExternalLink size={13} />}
          >
            {t('title.openInJellyfin')}
          </Button>
        ) : (
          <Tooltip
            label={
              link.reason === 'not_configured'
                ? t('title.jellyfinNotConfigured', {
                    missing: (link.missing ?? [])
                      .map((key) => t(`title.jellyfinField.${key}`))
                      .join(', '),
                  })
                : t(
                    link.reason === 'not_mapped'
                      ? 'title.jellyfinNotMapped'
                      : 'title.jellyfinNotFound',
                  )
            }
            multiline
            w={260}
          >
            {/* Обёртка нужна: у disabled-кнопки нет событий мыши, и подсказка
                без неё не всплывёт — то есть объяснение стало бы недоступно
                ровно тогда, когда оно и требуется. */}
            <span>
              <Button
                variant="default"
                size="compact-sm"
                leftSection={<IconDeviceTv size={14} />}
                disabled
              >
                {t('title.openInJellyfin')}
              </Button>
            </span>
          </Tooltip>
        ))}
    </Group>
  )
}
