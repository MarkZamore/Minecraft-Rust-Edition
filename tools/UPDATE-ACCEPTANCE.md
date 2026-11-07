# Обновление движка с сохранением обязательных правок

Проверяются две независимые границы. `check_required_layers.py` вычисляет
результат точного рецепта из подписанного архива и закреплённого Steam-кода.
Контракт проверяет 11 обязательных групп, включая авторизацию, lifecycle,
приглашения, меню/команды, скины, список миров, заголовок и значок. Комментарий
вместо вызова не проходит проверку. Это проверка исходников, а не доказательство
игрового поведения.

`release_acceptance.py` проверяет отчёты поведения, привязанные к полному
инвентарю исполняемых файлов/данных, upstream/Steam pins и квитанции рецепта.
Свежесть означает совпадение этих входов, а не возраст файла отчёта. HEAD,
описание библиотеки и превью не входят в эту привязку. Поэтому QA-only и
редакционные коммиты не заставляют повторять игровой прогон.

## Порядок

1. В чистом worktree перенести рецепт на новые подписанные исходники. Любой
   конфликт обязательного слоя остановит подготовку. Проверить исходники:

   ```powershell
   python tools/check_required_layers.py --archives $archives `
       --steam-transport $steamRepo/source/native --out tools/required-layer-proof.json
   ```

2. Собрать и получить обычную overlay-receipt; подготовить candidate штатным
   stage_runtime. Он повторно вычисляет послепроверки и сверяет результат
   с квитанцией и required-layer-proof. Архивы, код мода и Java/Mojang кеши
   переиспользовать; игровые папки держать отдельно в зарегистрированном стенде.

3. Проверить точный candidate через `smoke_runtime.py --bootstrap ... --cache ...
   --expect-title 'Minecraft Rust Edition' --expect-cold --check-pause-menu` без
   `--minecraft`/`--java`, затем `steam_runtime_qa.py`. Оба инструмента сохраняют
   acceptanceContext до запуска и проверяют неизменность файлов после прогона.
   Для неизменившегося подготовщика можно использовать прежний отдельный cold
   proof, только если SHA bootstrap/preparer/research и resource receipt совпадают.

4. Записать пакет доказательств; coldPreparation берётся из холодного smoke,
   либо отдельного --preparation. До проверки обновления --update можно опустить.

   ```powershell
   python tools/release_acceptance.py --candidate $candidate --smoke $smoke `
       --multiplayer $multiplayer --out tools/release-acceptance.json `
       --defer-live-steam 'Точное сохранённое решение пользователя'
   ```

   Отсрочка допустима только по действующему решению пользователя. После живого
   прогона вместо неё передать --live-steam-report: PASS, acceptanceContext и
   checks.shiftTab/invitation/twoAccountJoin = PASS. Результаты не выдумывать.

5. Принять inventory/preview/distribution lock обычным способом и закоммитить
   исходники. `generate_manifest.py --prepare-qa` создаст архивы и
   **candidate-manifest.json**, без выпускного pack-manifest. Передать эту папку
   и предыдущую поставку NativeUpdateQa: он читает оба имени манифеста. Проверить
   прерывание загрузки, блокировку клиента, отказ запуска неполного набора,
   повторную попытку и сохранность настроек/серверов/прогресса.

6. Повторить команду пункта 4 с --update $updateReport. Инвентарь targetFiles и
   SHA runtime-архива отчёта должны соответствовать всему выпускаемому набору,
   включая отдельный архив пресета. Сохранить доказательства в Git. Обычные
   generate_manifest и --verify-only теперь требуют все обязательные отчёты.
   После QA-only коммита SHA игровых архивов должен остаться прежним.

7. Опубликовать архивы, независимо скачать их и проверить SHA-256, затем
   опубликовать манифест последним и проверить его обратным скачиванием.

## Текущая принятая поставка

Начальная запись release-acceptance импортирована из сохранённых отчётов
опубликованной `bb8302b206ed158e85aefead08bb78cd96606ba2`, а не получена новым
игровым прогоном. Импорт сверил весь candidate с distribution lock этого коммита,
точный EXE/bootstrap, cold proof и гостевые случаи с принятым отчётом Git.
Для другой сборки/инвентаря такой импорт будет отклонён. Живой Steam остаётся
NOT RUN по решению пользователя от 8 ноября 2026 года.

Проверки защиты без игры/Cargo:

```powershell
python -m unittest discover -s tools -p 'test_required_layers.py' -v
python -m unittest discover -s tools -p 'test_release_acceptance.py' -v
```
