# Общий нативный runtime Rust для Windows

Общий `horizon-client.exe` и его DLL принадлежат одному SDK. Лаунчер выбирает закреплённую поставку этого SDK, а bootstrap задаёт процессу `LANMINECRAFT_NATIVE_PACK_ROOT` абсолютным путём к проверенной папке сборки. В собственном `native/` каждой сборки остаются `native-mods.json`, модовые DLL, `horizon-native-mods/` и её `.hmod` в `horizon-mods/`. Папка игры и миров задаются прежними параметрами запуска.

`apply.py` переносит точки чтения профиля на один корень с проверкой исходных SHA‑256. Запуск с явным профилем требует `native-mods.json` с ABI ID общего SDK. Пустой список библиотек сохраняет Rust Edition без модов. При отсутствии переменной штатный SDK ищет DLL и пакеты возле EXE, а отдельная установка обнаруживает свои контентные и Wasm-моды через каталог игры. Нативные библиотеки живут до выхода процесса. Смена профиля требует отдельного процесса.

## Воспроизведение Windows SDK

Передайте извлечённый и проверенный Horizon с обязательным слоем Edition, закреплённый подписанный исходный архив из `tools/upstream-lock.json`, исходники закреплённого транспорта Steam и один зарегистрированный стенд. `build_sdk.py --plan-only` проверяет дерево и выводит ABI ID. При сборке upstream SDK получает точные байты из закреплённого архива с проверенными слоями. Компилятор `1.99.1-x86_64-pc-windows-gnu` выбирается для дочерних процессов. Локальный ABI ID учитывает архив, состав слоёв и вывод `rustc -vV`. Авторский коммит Horizon закреплён отдельным полем.

```powershell
python -B integration/shared-native/build_sdk.py --source $horizonSource --source-archive $sourceArchive `
  --steam-transport $steamTransport --run $run --plan-only
```

Снятие `--plan-only` создаёт `$run/shared-sdk` и `$run/shared-sdk-build.json`. Упаковка общего компонента использует набор `slices.runtime` и подготовленные для него DLL Rust/MinGW по правилам upstream SDK. Подготовка ресурсов Minecraft, Java, Steam и представления сборки остаётся в существующем bootstrap.

Для общего SDK Edition и Mods передайте `--seams $seamReceipt`. Квитанция закрепляет SHA‑256 исходных и подготовленных файлов. Подготовленные файлы лежат в `files/` рядом с квитанцией. Рецепт добавляет проверенные точки замены поверх этих файлов. Регистрации и реализация модов устанавливаются из DLL выбранного профиля.

При сборке для Mods флаг `--export-patch-source` сохраняет в `$run/shared-sdk-patch-source` исходные модули из `functions.json` и шаблон регистрации DLL. Рецепт проверяет ABI и SHA‑256 каждого файла перед сохранением. Эти байты нужны при компиляции копий функций для `horizon_patch.dll`.

После прерванной сборки `--reuse-target` позволяет повторить ту же команду в том же стенде. Инструмент сверяет квитанцию входов и использует уже собранные зависимости Cargo.
Если изменился только слой адаптации, `--refresh-previous-abi $previousAbi` использует тот же кеш. Укажите ABI из предыдущей квитанции и освободите пути итогового SDK после сверки его отчёта. Исходный архив, целевая платформа и компилятор должны совпадать.

## Архив общего runtime

Подготовьте `$run/common-candidate/native` из файлов действующего утверждённого Edition без прежнего `horizon-client.exe` и `presentation.json`. Добавьте новый bootstrap, закреплённый в `tools/bootstrap-lock.json`. Затем соберите спецификацию семейства и передайте её упаковщику лаунчера.

```powershell
python -B integration/shared-native/stage_payload.py --run $run --base-lock $previousEditionLock `
  --id $runtimeId --version $runtimeVersion --minecraft-version 26.3 `
  --source $releaseOwner $releaseRepository $runtimeTag
python -B "$launcher/tools/native_runtime_release.py" stage-runtime `
  --candidate "$run/common-candidate" --spec "$run/shared-runtime-spec.json" --out $runtimeDist
```

В архив попадают клиент, DLL из проверенного среза SDK, закреплённые DLL Rust и MinGW и точные общие файлы Edition. Манифесты профилей привязывайте к одному архиву через `bind-descriptor`. Порядок игровой приёмки и публикации описан в `tools/NATIVE-RUNTIME-RELEASE.md` репозитория лаунчера.

## Профили и совместимость

`profile.py` берёт квитанцию точного SDK и DLL, собранные с его динамическими библиотеками и идентичностью, записанной в `*.identity.json`. Передайте каждую DLL через `--library` в порядке зависимостей. При наличии `horizon_patch.dll` инструмент ставит его после модов. Для Edition достаточно подготовленной папки `native/` без аргументов `--library`.

```powershell
python -B integration/shared-native/profile.py --sdk "$run/shared-sdk" `
  --sdk-receipt "$run/shared-sdk-build.json" --pack-root $editionStage
```

Официальный ABI различает моды на типизированном `Hooks` и моды с заменой функций. Первые компилируются в отдельные DLL. Вторые сохраняют один и тот же клиент и дополняют профиль `horizon_patch.dll` с копиями функций, составленными для этого профиля. Игра проверяет экспортированную ABI идентичность и хеш DLL до регистрации. Бинарники других сборок Horizon или Rust ABI требуют повторной сборки. Для нового состояния используются вложения или состояние самого мода. Изменение полей структур требует пересборки SDK.

Из 37 компонентов Rust Mods 34 используют прежний формат mixin, `skins_3d` и `spider_man` уже несут нативные исходные пакеты, `zoom` использует Wasm. `slots.py` читает подготовленные mixin-манифесты профиля и сравнивает цели нижних крейтов с `functions.json` общего SDK. В текущем составе 32 таких внедрения в 26 уникальных функций. `lower_slots.py` закрепляет их исходные файлы и проверяет точки замены и хеши тел в каталоге, который создал `horizon-stage`. Tectonic направляет внедрение `snow-height` в свободную функцию `height_adjusted`, которую вызывает исходный метод `BiomeTemperatureSettings::height_adjusted`.

```powershell
python -B integration/shared-native/slots.py --bundles $modsMixinsInput
python -B integration/shared-native/slots.py --bundles $modsMixinsInput `
  --functions "$run/shared-sdk/functions.json" --build-id $abiBuildId --require
```

Для выпуска Rust Mods на этих DLL требуется полный перенос старых деклараций, рендера, физики, Tectonic и серверной обработки Orbital, затем обе игровые приёмки на одних байтах SDK.
