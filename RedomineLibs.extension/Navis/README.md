# Плагины Navisworks 2024

В каталоге `RedomineLibs.extension/Navis` — два соседних независимых проекта.
Каждый собирается в одну общую папку `bin/Release/net48`, чтобы обе DLL
лежали рядом:

| DLL | Где исходники | Плагин(ы) внутри | Описание |
|-----|---------------|------------------|----------|
| `NavisClashOrder.dll`     | `ClashOrder/`     | `Redomine.ClashOrder`       | Переставляет верхнеуровневые clash tests активного документа в порядке, заданном элементами `clashtest` файла `Clash Detective.xml`. |
| `NavisSearchSetRename.dll` | `SearchSetRename/`| `Redomine.SearchSetRename`  | В поисковых наборах, имя которых начинается с `ВК1 В` или `ВК2 К`, заменяет во всех критериях поиска имя свойства `ФОП_ВИС_Имя системы` на `Имя системы`. Остальные настройки критерия и самих наборов сохраняются. |

## Запуск через AddInManager

1. Откройте проект в Navisworks Manage 2024.
2. В AddInManager загрузите нужную DLL из `bin\Release\net48`:
   - `NavisClashOrder.dll` → `Redomine.ClashOrder`
   - `NavisSearchSetRename.dll` → `Redomine.SearchSetRename`
3. Запустите нужный плагин.

Файл `Clash Detective.xml` (для плагина ClashOrder) должен лежать рядом с
`NavisClashOrder.dll`. При сборке он копируется туда автоматически.

## Сборка

Из папки `RedomineLibs.extension/Navis` соберите оба проекта:

```powershell
dotnet build .\ClashOrder\NavisClashOrder.csproj -c Release
dotnet build .\SearchSetRename\NavisSearchSetRename.csproj -c Release
```

Обе DLL (`NavisClashOrder.dll` и `NavisSearchSetRename.dll`) окажутся в одной
папке `bin\Release/net48`, файл `Clash Detective.xml` — рядом с ними.
Obj/bin каждого проекта хранятся в собственной подпапке.

## Тесты

Чистая логика переименования вынесена в
`SearchSetRename\SearchSetRenameLogic.cs` и покрыта тестами в
`SearchSetRename\tests\SearchSetRenameLogicTests.cs`. Запуск:

```powershell
dotnet build .\SearchSetRename\tests\NavisSearchSetRenameTests.csproj -c Release
.\SearchSetRename\tests\bin\Release\net48\NavisSearchSetRenameTests.exe
```

Тесты не зависят от Navis API: они проверяют отбор имён поисковых наборов
по префиксам `ВК1 В` / `ВК2 К` и формирование нового комбинированного имени
свойства для `NamedConstant`.
