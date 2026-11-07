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

В конце сохраняется `report.json` с SHA-256 манифестов и архивов, идентификатором и SHA-256 сборки лаунчера, числами попыток и итогами проверок. Сервис получает release assets из локального HTTP-обработчика. Игра и интерфейс не запускаются. Техническая установка удаляется в `finally`.

Переход с `94deaa18` на `ac07af4` проверен на точных байтах обеих поставок. [Отчёт проверки](edition-static-report.json)

Сценарий также принимает обновление всего движка: вычисляет изменённые и удалённые управляемые файлы по двум манифестам, проверяет объём загрузки выбранных архивов и сохраняет времена неизменных файлов. Если меняется клиент, блокируется именно `native/horizon-client.exe`. Настройки, ручные серверы, прогресс и скриншоты представлены синтетическими метками; проверка сохранения настоящего мира выполняется отдельно игровым smoke.

При заблокированном клиенте проверяется журналируемое незавершённое применение: каждый уже размещённый файл имеет точный прежний или новый SHA-256, прежний EXE цел, целевая ревизия не объявлена завершённой, подготовка игры запрещена. Повторная попытка считается по оставшимся файлам и должна восстановить весь целевой снимок.
