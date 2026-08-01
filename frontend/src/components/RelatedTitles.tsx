import { Anchor, Badge, Card, Collapse, Group, Loader, Text, UnstyledButton } from '@mantine/core'
import { IconChevronDown, IconExternalLink } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { titleApi } from '../api/title'
import { useT } from '../i18n'
import { useTitleName } from '../i18n/titles'
import classes from './RelatedTitles.module.css'

interface Props {
  seriesId: number
}

/**
 * Порядок просмотра франшизы.
 *
 * Свёрнут по умолчанию: у Re:Zero в линейке полтора десятка позиций, и
 * развёрнутый список оттеснил бы вниз то, ради чего страницу открывают —
 * переводы и серии.
 *
 * Связи приходят с Шикимори: в API Anime365 их нет вовсе. Узлы, которых нет в
 * нашем каталоге, показываются без внутренней ссылки, но со ссылкой наружу —
 * знать, что продолжение существует, полезно и в этом случае.
 */
export function RelatedTitles({ seriesId }: Props) {
  const { t } = useT()
  const titleName = useTitleName()
  const [opened, setOpened] = useState(false)

  const query = useQuery({
    queryKey: ['relations', seriesId],
    queryFn: () => titleApi.relations(seriesId),
    // Запрос уходит на чужой сервис — только по явному раскрытию блока.
    enabled: opened,
    staleTime: 24 * 60 * 60 * 1000,
    // Недоступность Шикимори не должна выглядеть как поломка страницы.
    retry: false,
  })

  const nodes = query.data?.nodes ?? []

  return (
    <Card withBorder padding="sm" style={{ flexShrink: 0 }}>
      <UnstyledButton
        onClick={() => setOpened((value) => !value)}
        className={classes.header}
        aria-expanded={opened}
      >
        <Group gap="xs" wrap="nowrap">
          <Text fw={600} size="sm">
            {t('related.title')}
          </Text>
          {query.isFetching && <Loader size="xs" />}
          {nodes.length > 0 && (
            <Text size="xs" c="dimmed">
              {t('related.count', { count: nodes.length })}
            </Text>
          )}
        </Group>
        <IconChevronDown
          size={16}
          className={classes.chevron}
          data-opened={opened || undefined}
        />
      </UnstyledButton>

      <Collapse in={opened}>
        {/* ⚠️ Заглушка на время загрузки обязательна. Collapse измеряет высоту
            содержимого в момент раскрытия, а данные приходят позже: при первом
            клике внутри пусто, высота получается нулевой, и блок выглядит
            закрытым. Открыть его удавалось только с третьего нажатия — к тому
            времени содержимое уже было. */}
        {(query.isLoading || query.isFetching) && nodes.length === 0 && (
          <Group gap="xs" mt="xs">
            <Loader size="xs" />
            <Text size="xs" c="dimmed">
              {t('related.loading')}
            </Text>
          </Group>
        )}

        {query.isError && (
          <Text size="xs" c="dimmed" mt="xs">
            {t('related.unavailable')}
          </Text>
        )}

        {query.isSuccess && nodes.length === 0 && (
          <Text size="xs" c="dimmed" mt="xs">
            {t('related.none')}
          </Text>
        )}

        {nodes.length > 0 && (
          <div className={classes.list}>
            {nodes.map((node) => {
              const current = node.series_id === seriesId
              // Своё название показываем на языке интерфейса; у чужих его нет,
              // остаётся имя с Шикимори.
              const label = node.titles
                ? titleName.primary(node.titles, node.name ?? '')
                : node.name

              return (
                <div key={node.mal_id} className={classes.row} data-current={current || undefined}>
                  {/* Мини-постер: строка из одного текста плохо читается, а
                      обложку глаз узнаёт быстрее названия. Тайтлы, которых нет
                      в нашем каталоге, остаются без картинки — постер берётся
                      оттуда, а не с Шикимори. */}
                  <div className={classes.poster}>
                    {node.poster_url_small && (
                      <img src={node.poster_url_small} alt="" loading="lazy" />
                    )}
                  </div>

                  <Text size="xs" c="dimmed" className={classes.year}>
                    {node.year ?? '—'}
                  </Text>

                  {node.kind && (
                    <Badge size="xs" variant="light" className={classes.kind}>
                      {node.kind}
                    </Badge>
                  )}

                  {/* Текущий тайтл ссылкой не делаем: клик по самому себе
                      выглядит как поломка навигации. */}
                  {current ? (
                    <Text size="sm" fw={600} className={classes.name}>
                      {label}
                    </Text>
                  ) : node.series_id ? (
                    <Anchor component={Link} to={`/series/${node.series_id}`} size="sm" className={classes.name}>
                      {label}
                    </Anchor>
                  ) : (
                    <Group gap={4} wrap="nowrap" className={classes.name}>
                      <Text size="sm" c="dimmed">
                        {label}
                      </Text>
                      {node.shikimori_url && (
                        <Anchor
                          href={node.shikimori_url}
                          target="_blank"
                          rel="noreferrer noopener"
                          size="xs"
                          title={t('related.notInCatalog')}
                        >
                          <IconExternalLink size={12} />
                        </Anchor>
                      )}
                    </Group>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </Collapse>
    </Card>
  )
}
