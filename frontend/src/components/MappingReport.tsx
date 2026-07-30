import { Badge, Card, Group, Progress, Stack, Table, Text, Tooltip } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { libraryApi, SOURCE_COLORS, SOURCE_LABELS } from '../api/library'
import { ErrorState } from './ErrorState'
import pathClasses from './PathLine.module.css'

/**
 * Чем именно смапились папки и с какой уверенностью.
 *
 * Пороги нечёткого совпадения подкручиваются по фактам, а не на глаз: если
 * почти всё легло у самой границы — порог стоит поднять, если много
 * неразобранного — опустить.
 */
export function MappingReport() {
  const report = useQuery({
    queryKey: ['library-mapping-report'],
    queryFn: libraryApi.mappingReport,
  })

  if (report.isError) {
    return (
      <ErrorState
        compact
        error={report.error}
        title="Не удалось построить отчёт"
        onRetry={() => report.refetch()}
      />
    )
  }

  const data = report.data
  if (!data) return null

  const totalMappings = data.by_source.reduce((sum, row) => sum + row.count, 0)
  if (totalMappings === 0) {
    return (
      <Text size="sm" c="dimmed">
        Пока ни одна папка не привязана. Запустите сканирование.
      </Text>
    )
  }

  return (
    <Stack gap="md">
      {/* align сверху: карточки разной высоты, а по центру они выглядят
          съехавшими друг относительно друга. */}
      <Group gap="md" wrap="wrap" align="flex-start">
        <Card withBorder padding="sm" miw={220} style={{ flexShrink: 0 }}>
          <Text size="sm" fw={600} mb={6}>
            Чем найдено
          </Text>
          <Stack gap={6}>
            {data.by_source.map((row) => (
              <div key={row.source}>
                <Group justify="space-between" gap="xs" mb={2}>
                  <Badge size="xs" variant="light" color={SOURCE_COLORS[row.source] ?? 'gray'}>
                    {SOURCE_LABELS[row.source] ?? row.source}
                  </Badge>
                  <Text size="xs" c="dimmed">
                    {row.count}
                  </Text>
                </Group>
                <Progress
                  value={(row.count / totalMappings) * 100}
                  size="sm"
                  color={SOURCE_COLORS[row.source] ?? 'gray'}
                />
              </div>
            ))}
          </Stack>
        </Card>

        <Card withBorder padding="sm" miw={220} style={{ flexShrink: 0 }}>
          <Text size="sm" fw={600} mb={6}>
            Уверенность
          </Text>
          <Stack gap={6}>
            {data.confidence_buckets.map((bucket) => (
              <div key={bucket.range}>
                <Group justify="space-between" gap="xs" mb={2}>
                  <Text size="xs">{bucket.range}</Text>
                  <Text size="xs" c="dimmed">
                    {bucket.count}
                  </Text>
                </Group>
                <Progress
                  value={(bucket.count / totalMappings) * 100}
                  size="sm"
                  // Всё, что ниже 90, стоит просмотреть глазами.
                  color={Number(bucket.range.split('–')[0]) >= 90 ? 'teal' : 'yellow'}
                />
              </div>
            ))}
          </Stack>
        </Card>

        <Card withBorder padding="sm" miw={200} style={{ flexShrink: 0 }}>
          <Text size="sm" fw={600} mb={6}>
            Итого
          </Text>
          <Stack gap={2}>
            <Text size="sm">Папок всего: {data.folders_total}</Text>
            <Text size="sm">Привязано: {data.folders_mapped}</Text>
            <Text size="sm" c={data.folders_unmapped > 0 ? 'orange' : undefined}>
              Без привязки: {data.folders_unmapped}
            </Text>
            <Text size="sm" c={data.needs_confirmation > 0 ? 'yellow' : undefined}>
              Ждут подтверждения: {data.needs_confirmation}
            </Text>
            <Text size="sm" c="dimmed">
              Вручную: {data.manual}
            </Text>
          </Stack>
        </Card>
      </Group>

      {data.weakest.length > 0 && (
        <div>
          <Text size="sm" fw={600} mb={4}>
            Самые слабые связи
          </Text>
          <Text size="xs" c="dimmed" mb="xs">
            С них разумно начинать ручную проверку: если ошибка есть, она скорее здесь.
          </Text>
          <Table.ScrollContainer minWidth={520}>
            <Table striped highlightOnHover verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Папка</Table.Th>
                  <Table.Th>Тайтл</Table.Th>
                  <Table.Th w={150}>Чем определено</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.weakest.map((row) => (
                  <Table.Tr key={row.mapping_id}>
                    <Table.Td>
                      <Text size="sm" className={pathClasses.pathLine} title={row.folder}>
                        {row.folder}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" component={Link} to={`/series/${row.series_id}`} c="blue">
                        {row.series_title}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Tooltip label={row.matched_on ?? 'без пояснения'}>
                        <Badge
                          size="xs"
                          variant="light"
                          color={SOURCE_COLORS[row.source] ?? 'gray'}
                        >
                          {SOURCE_LABELS[row.source] ?? row.source} · {Math.round(row.confidence)}%
                        </Badge>
                      </Tooltip>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </div>
      )}
    </Stack>
  )
}
