/*
 * Выставляет тему до первой отрисовки.
 *
 * Без этого страница успевает нарисоваться светлой (значение по умолчанию в
 * стилях Mantine), и только потом React проставляет атрибут — на тёмной теме
 * это заметная белая вспышка при каждой загрузке.
 *
 * Почему отдельный файл, а не встроенный <script>: CSP запрещает инлайновые
 * скрипты (script-src 'self' без 'unsafe-inline'), поэтому штатный
 * <ColorSchemeScript /> из Mantine, который вставляет код прямо в разметку,
 * браузер бы заблокировал. Логика повторяет его дословно, включая имя ключа
 * в localStorage — им же пользуется сам Mantine.
 */
;(function () {
  try {
    var stored = window.localStorage.getItem('mantine-color-scheme-value')
    var scheme =
      stored === 'light' || stored === 'dark' || stored === 'auto' ? stored : 'auto'
    var computed =
      scheme !== 'auto'
        ? scheme
        : window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light'
    document.documentElement.setAttribute('data-mantine-color-scheme', computed)
  } catch (e) {
    // Приватный режим или заблокированное хранилище — тема просто останется
    // той, что задана по умолчанию. Падать здесь нельзя: скрипт выполняется
    // до приложения, и исключение оставило бы пустую страницу.
  }
})()
