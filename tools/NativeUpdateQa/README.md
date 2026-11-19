# Проверка обновления нативной сборки

Стенд использует `PortablePackSyncService` и `PackInstanceService` из исходников LANMinecraft. Он устанавливает исходный архив Edition в техническую папку, обрывает загрузку обновления после первого байта, блокирует замену изменившегося нативного файла и проверяет восстановление по повторному запросу. Настройки и мир представлены синтетическими метками.

Перед запуском выполните `python tools/workspace_storage.py --check` из LANMinecraft. Используйте существующий зарегистрированный стенд, где `dist` и `dist-static` содержат точные архивы двух ревизий. Нужны Windows и .NET 10. Запустите команды из репозитория Minecraft Rust Edition, указав рабочие пути.

```powershell
$launcher = (Resolve-Path ../LANMinecraft).Path
$run = (Resolve-Path (Join-Path $launcher 'artifacts/agent-runs/minecraft-rust-edition')).Path
$project = 'tools/NativeUpdateQa/NativeUpdateQa.csproj'
$build = Join-Path $run 'edition-update-build'
$revision = git -C $launcher rev-parse HEAD
dotnet build $project --artifacts-path $build -p:LauncherRoot=$launcher `
    -p:SourceRevisionId=$revision -p:SkipPortableIdentityAdapter=true
$qa = Get-ChildItem (Join-Path $build 'bin/NativeUpdateQa') -Recurse -Filter NativeUpdateQa.dll |
    Select-Object -First 1
dotnet $qa.FullName $launcher (Join-Path $run 'dist') (Join-Path $run 'dist-static') `
    (Join-Path $run 'edition-update-qa')
```

В конце сохраняется `report.json` с SHA-256 манифестов и архивов, идентификатором и SHA-256 сборки лаунчера, числами попыток и итогами проверок. Все установленные нативные EXE и DLL проверяются по заголовкам Windows x64. Сервис получает release assets из локального HTTP-обработчика. В офлайн-сценарии обработчик возвращает ошибку для прямого адреса и всех попыток через GitHub API. Игра и интерфейс не запускаются. Техническая установка удаляется в `finally`.

Для локальной проверки механизма обновления по уже сверенным экспериментальным ZIP можно передать `launcher-update-fixture.json` и каталог этих ZIP. Оба пути должны находиться в `artifacts/agent-runs`.

```powershell
dotnet $qa.FullName $launcher $oldDist $fixtureJson $testOutput --launcher-fixture $verifiedArchives
```

Успешный эксперимент получает `FIXTURE_PASS`. Релизный генератор требует отчёт со статусом `PASS` по выпускному комплекту архивов.

Для отдельной проверки запуска через WPF заранее зарегистрируйте один стенд с `RUN.json`. Подготовьте каждую сборку из её собственного каталога поставки.

```powershell
dotnet $qa.FullName --install-for-launcher $launcher $editionDist $run
dotnet $qa.FullName --install-for-launcher $launcher $modsDist $run
```

Команда использует штатную установку с локальным обработчиком архивов. Манифест, архивы и установленные файлы сверяются по SHA-256. Оба кандидата попадают в `launcher-installation/Minecraft/Packs` с суффиксом ` QA` и проверенной квитанцией установки. Личный путь позволяет штатной кнопке «Играть» использовать точного кандидата до его публикации. Рядом с установкой остаются отчёты `minecraft-9-installed.json` и `minecraft-10-installed.json`.

Положите проверенный `LANMinecraft.exe` в `launcher-installation`, запустите его, выберите каждый QA-вариант и нажмите «Играть». Для каждой сборки зафиксируйте версию и SHA-256 лаунчера, дочерний процесс из стенда, отзывчивое окно, первый кадр в `launcher-console.log` и результат завершения. Закройте проверяемую игру перед запуском следующей. Этот сценарий оставляет тестовую установку для запуска, поэтому после фиксации результатов удалите собственную копию `launcher-installation`.

Переход с `94deaa18` на `ac07af4` проверен на точных байтах обеих поставок. [Отчёт проверки](edition-static-report.json)

Сценарий также принимает обновление всего движка: вычисляет изменённые и удалённые управляемые файлы по двум манифестам, проверяет объём загрузки выбранных архивов и сохраняет времена неизменных файлов. Если меняется клиент, блокируется именно `native/horizon-client.exe`. Настройки, ручные серверы, прогресс и скриншоты представлены синтетическими метками; проверка сохранения настоящего мира выполняется отдельно игровым smoke.

При заблокированном клиенте проверяется журналируемое незавершённое применение: каждый уже размещённый файл имеет точный прежний или новый SHA-256, прежний EXE цел, целевая ревизия не объявлена завершённой, подготовка игры запрещена. Повторная попытка считается по оставшимся файлам и должна восстановить весь целевой снимок.
