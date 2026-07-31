import { Alert, Badge, Button, Group, Stack, Table, Text } from '@mantine/core'
import { IconDeviceTv, IconRefresh } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'

import { libraryApi } from '../api/library'
import { useT } from '../i18n'
import { useFormat } from '../i18n/format'
import pathClasses from './PathLine.module.css'

/**
 * Что из нашей библиотеки Jellyfin не распознал.
 *
 * Готового ответа у Jellyfin нет — он не сообщает, что пропустил, он просто
 * этого не показывает. Считается разностью множеств на нашей стороне, поэтому
 * запрос дорогой (обход его библиотеки целиком) и запускается кнопкой, а не
 * при открытии вкладки.
 *
 * Показываем сначала сводку по папкам, потом файлы. Нераспознанное почти всегда
 * идёт группами по одной причине — имя, которое Jellyfin не разбирает, — и
 * закономерность видна по папкам, а не по списку из трёхсот имён.
 */
export function JellyfinAudit() {
  const { t } = useT()
  const format = useFormat()

  const audit = useQuery({
    queryKey: ['jellyfin-audit'],
    queryFn: libraryApi.jellyfinAudit,
    // Только по кнопке: обход чужой библиотеки не должен запускаться от того,
    // что человек заглянул на вкладку.
    enabled: false,
    retry: false,
  })

  const data = audit.data

  return (
    <Stack gap="sm">
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Text size="sm" c="dimmed" style={{ flex: 1, minWidth: 240 }}>
          {t('library.jellyfinAuditHint')}
        </Text>
        <Button
          size="compact-sm"
          variant="light"
          leftSection={audit.isFetching ? <IconRefresh size={14} /> : <IconDeviceTv size={14} />}
          loading={audit.isFetching}
          onClick={() => void audit.refetch()}
        >
          {t('library.jellyfinAuditRun')}
        </Button>
      </Group>

      {audit.isError && <Alert color="red">{t('library.jellyfinAuditFailed')}</Alert>}

      {data && !data.available && (
        <Alert color={data.reason === 'unreachable' ? 'red' : 'yellow'}>
          {t(`library.jellyfinAuditReason.${data.reason}` as const)}
        </Alert>
      )}

      {data?.available && (
        <>
          <Group gap="xs">
            <Badge color="gray" variant="light">
              {t('library.jellyfinAuditOurs', { count: data.our_files })}
            </Badge>
            <Badge color="gray" variant="light">
              {t('library.jellyfinAuditTheirs', { count: data.jellyfin_files })}
            </Badge>
            <Badge color={data.missing_count > 0 ? 'orange' : 'teal'}>
              {t('library.jellyfinAuditMissing', { count: data.missing_count })}
            </Badge>
          </Group>

          {data.missing_count === 0 ? (
            <Alert color="teal">{t('library.jellyfinAuditAllGood')}</Alert>
          ) : (
            <>
              {/* Сводка по папкам идёт первой: она отвечает на вопрос «почему»,
                  а список файлов — только на «что именно». */}
              <Table striped highlightOnHover verticalSpacing="xs">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{t('library.columnFolder')}</Table.Th>
                    <Table.Th w={120}>{t('library.jellyfinAuditColumnCount')}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {data.by_folder.map((row) => (
                    <Table.Tr key={row.folder}>
                      <Table.Td>
                        <Text size="xs" className={pathClasses.pathLine}>
                          {row.folder}
                        </Text>
                      </Table.Td>
                      <Table.Td>{row.count}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>

              <Table.ScrollContainer minWidth={520}>
                <Table striped verticalSpacing="xs">
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>{t('library.jellyfinAuditColumnFile')}</Table.Th>
                      <Table.Th w={100}>{t('library.columnSize')}</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {data.missing.map((file) => (
                      <Table.Tr key={file.id}>
                        <Table.Td>
                          <Text size="xs" className={pathClasses.pathLine}>
                            {file.rel_path}
                          </Text>
                        </Table.Td>
                        <Table.Td>{format.size(file.size_bytes)}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>

              {data.truncated && (
                <Text size="xs" c="dimmed">
                  {t('library.jellyfinAuditTruncated', { shown: data.missing.length })}
                </Text>
              )}
            </>
          )}
        </>
      )}
    </Stack>
  )
}
