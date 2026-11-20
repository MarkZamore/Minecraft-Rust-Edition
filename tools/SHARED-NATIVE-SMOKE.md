# Проверка общего Windows-клиента

Используйте один зарегистрированный стенд с проверенными `shared-sdk/` и `shared-sdk-build.json`. В `overlays/` подготовьте обычные каталоги сборок с `portable-pack.json` и `native/presentation.json`. Для пустой Edition и технического профиля с одним Zoom вызовите `profile.py` без DLL. В техническом профиле положите закреплённый `zoom.hmod` (ID `zoom`, версия `3.0.3`) в `native/horizon-mods/`. SHA‑256 пакета `53935d0af3307bbe8fa5057103a62b13a8cb6a69431b3058f3981b9c1313d348`.

Команды из корня LANMinecraft используют уже подготовленные каталоги стенда. `$run` указывает на активный стенд. `$runtimeDist` содержит контракт и ZIP от `tools/native_runtime_release.py stage-runtime`. `$runtime` указывает на единственное распакованное дерево runtime с именем каталога, равным SHA‑256 ZIP. Для локального кандидата переименуйте существующий `common-candidate` на этот SHA после упаковки. `$controller` указывает на проверенный `horizon-ctl.exe` той же игровой ревизии.

```powershell
$edition = Join-Path (Get-Location).Path 'LocalSources/Minecraft Rust Edition'
$sdk = Join-Path $run 'shared-sdk'
$receipt = Join-Path $run 'shared-sdk-build.json'
$packRoots = @(
    (Join-Path $run 'overlays/Minecraft Rust Edition')
    (Join-Path $run 'overlays/Rust Future Zoom QA')
)
foreach ($overlay in $packRoots) {
    python -B "$edition/integration/shared-native/profile.py" --sdk $sdk --sdk-receipt $receipt --pack-root $overlay
    if ($LASTEXITCODE) { throw 'Native profile staging failed' }
    $portable = Join-Path $overlay 'portable-pack.json'
    $bound = python -B tools/native_runtime_release.py bind-descriptor --descriptor $portable --runtime $runtimeDist
    if ($LASTEXITCODE) { throw 'Portable descriptor binding failed' }
    [IO.File]::WriteAllText($portable, ($bound -join "`n") + "`n")
}
```

У каждого технического профиля своё название окна в `native/presentation.json:name`, значение `nativeRuntime.version` и мир. Сохраните по одному JSON на профиль в `$run/smoke-inputs/`. Ниже пример Zoom. Для Edition укажите её путь и название, новый мир и `"codeMods": {}`.

```json
{
  "id": "future-zoom",
  "packRoot": "../overlays/Rust Future Zoom QA",
  "title": "Rust Future Zoom QA",
  "world": "ZoomSharedSmoke",
  "seed": 137249,
  "codeMods": { "zoom": "3.0.3" },
  "contentMods": {}
}
```

Для полного состава Mods используйте его подготовленный профиль и передайте каждую принятую библиотеку через `profile.py --library`. Укажите в отдельном входном JSON ожидаемые ID и версии нативного кода и Wasm в `codeMods`, а чисто контентных модов в `contentMods`. Добавьте входной JSON ещё одним аргументом `--profile`.

```powershell
$contract = Get-Content -LiteralPath "$runtimeDist/shared-native-runtime.json" -Raw | ConvertFrom-Json
$bootstrap = Get-Content -LiteralPath "$edition/tools/bootstrap-lock.json" -Raw | ConvertFrom-Json
$build = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
$qaArgs = @(
    '--runtime', $runtime, '--runtime-sha256', $contract.archive.sha256
    '--bootstrap-sha256', $bootstrap.executable.sha256, '--client-sha256', $build.clientSha256
    '--controller', $controller, '--prepared-cache', (Join-Path $run 'prepared-smoke')
    '--qa', (Join-Path $run 'shared-smoke')
    '--profile', (Join-Path $run 'smoke-inputs/edition.json')
    '--profile', (Join-Path $run 'smoke-inputs/future-zoom.json')
)
python -B "$edition/tools/smoke_shared_native.py" @qaArgs --verify-only
```

`--verify-only` проверяет общие файлы, дескрипторы и закреплённые DLL профилей. Для игровой приёмки запустите ту же команду без `--verify-only` с разрешением на запуск клиентов. При наличии проверенных серверного JAR, Java или входных данных Minecraft передайте их через `--server-jar`, `--java-home` и `--minecraft-dir`. Эти пути задают источники подготовки, а игровой мир создаётся во временном каталоге своего профиля.

В `report.json` проверяются точный путь и SHA‑256 запущенного EXE, цепочка процессов, заголовки окон, раздельные миры и списки загруженных модов из сохранений. Каждая следующая сборка должна использовать тот же путь к подготовленным ресурсам и ту же квитанцию с сохранённым временем записи. Сценарий удаляет собственные тестовые миры и кеши сессий после завершения.
