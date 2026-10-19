# Порядок клэш-тестов для Navisworks 2024

Плагин переставляет верхнеуровневые clash tests активного документа в порядке,
заданном элементами `clashtest` файла `Clash Detective.xml`.

- Сопоставление выполняется по точному имени с учётом регистра.
- Тесты без пары игнорируются и сохраняют свои исходные позиции.
- Используется `DocumentClashTests.TestsMove`; содержимое тестов не заменяется.

## Запуск через AddInManager

1. Откройте проект в Navisworks Manage 2024.
2. В AddInManager загрузите `bin\Release\net48\NavisClashOrder.dll`.
3. Запустите `Redomine.ClashOrder`.

Файл `Clash Detective.xml` должен лежать рядом с DLL. При сборке проекта он
копируется туда автоматически.

## Сборка

Из папки `RedomineLibs.extension\Navis` выполните:

```powershell
dotnet build .\NavisClashOrder.csproj -c Release
```

Команда создаст DLL и копию XML в `bin\Release\net48`.
