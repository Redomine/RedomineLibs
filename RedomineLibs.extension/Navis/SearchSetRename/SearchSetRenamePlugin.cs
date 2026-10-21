using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows.Forms;
using Autodesk.Navisworks.Api;
using Autodesk.Navisworks.Api.DocumentParts;
using Autodesk.Navisworks.Api.Plugins;

namespace Redomine.NavisSearchSetRename
{
    /// <summary>
    /// Плагин Navisworks: в поисковых наборах, имя которых начинается с ВК1" "В
    /// или ВК2" "К, заменяет во всех критериях поиска имя свойства
    /// ФОП_ВИС_Имя" "системы на Имя" "системы. Остальные настройки критерия
    /// и самих наборов сохраняются.
    /// </summary>
    [Plugin("Redomine.SearchSetRename", "RDMN2", DisplayName = "Переименование критериев поисковых наборов")]
    public sealed class SearchSetRenamePlugin : AddInPlugin
    {
        public override int Execute(params string[] parameters)
        {
            try
            {
                Document document = Autodesk.Navisworks.Api.Application.ActiveDocument;
                if (document == null)
                {
                    Show("Нет активного документа Navisworks.", MessageBoxIcon.Warning);
                    return 1;
                }

                RenameResult result = Run(document, out int processedSets, out int renamedConditionsCount);

                Show(
                    "Переименование критериев завершено.\n\n" +
                    "Обработано наборов: " + processedSets + "\n" +
                    "Переименовано критериев: " + renamedConditionsCount + "\n" +
                    "Наборов без изменений: " + result.Unchanged + "\n" +
                    "Наборов без поиска: " + result.WithoutSearch,
                    MessageBoxIcon.Information);
                return 0;
            }
            catch (Exception exception)
            {
                Show("Не удалось переименовать критерии поисковых наборов.\n\n" + exception.Message,
                    MessageBoxIcon.Error);
                return 1;
            }
        }

        /// <summary>
        /// Точка входа для тестов и вызовов вне UI: выполняет переименование в переданном документе.
        /// </summary>
        internal static RenameResult Run(Document document, out int processedSets, out int renamedConditionsCount)
        {
            if (document == null)
            {
                throw new ArgumentNullException(nameof(document));
            }

            DocumentSelectionSets selectionSets = document.SelectionSets;
            FolderItem rootItem = selectionSets.RootItem;

            // Собираем все листовые наборы с подходящим именем, не модифицируя дерево во время обхода.
            var targets = new List<TargetSet>();
            Walk(rootItem, accumulate: targets);

            processedSets = 0;
            renamedConditionsCount = 0;
            int unchanged = 0;
            int withoutSearch = 0;

            Transaction transaction = null;
            try
            {
                bool startedTransaction = false;
                if (!document.IsActiveTransaction)
                {
                    transaction = new Transaction(document, "Redomine: переименование критериев поисковых наборов");
                    startedTransaction = true;
                }

                foreach (TargetSet target in targets)
                {
                    SelectionSet selectionSet = target.SavedItem as SelectionSet;
                    if (selectionSet == null || !selectionSet.HasSearch)
                    {
                        withoutSearch++;
                        continue;
                    }

                    Search originalSearch = selectionSet.Search;
                    int changedInSet = BuildRenamedConditions(originalSearch, out IEnumerable<SearchCondition> renamedConditions);
                    if (renamedConditions == null || changedInSet == 0)
                    {
                        unchanged++;
                        continue;
                    }

                    // SavedItem.CreateCopy() возвращает независимую копию со всеми метаданными
                    // (GUID, DisplayName, Comments, Selection и т.д.). После замены в дереве
                    // старая ссылка уже не нужна — она не используется.
                    SavedItem createdCopy = selectionSet.CreateCopy();
                    SelectionSet replacement = createdCopy as SelectionSet;
                    if (replacement == null)
                    {
                        // На случай если CreateCopy вернул нетипизированный SavedItem.
                        replacement = new SelectionSet(selectionSet);
                    }

                    // Запоминаем комментарии исходного набора — CreateCopy теоретически
                    // сохраняет их, но EditComments требует элемент, уже состоящий в дереве.
                    CommentCollection originalComments = selectionSet.Comments;

                    // Копия поиска — сохраняет Locations, PruneBelowMatch, Selection и т.д.
                    Search newSearch = new Search(originalSearch);
                    newSearch.SearchConditions.CopyFrom(renamedConditions);

                    replacement.CopyFrom(newSearch);

                    if (target.ParentGroup == null)
                    {
                        selectionSets.ReplaceWithCopy(target.Index, replacement);
                    }
                    else
                    {
                        selectionSets.ReplaceWithCopy(target.ParentGroup, target.Index, replacement);
                    }

                    // Гарантируем сохранность комментариев: получаем уже подставленный в
                    // дерево элемент и при необходимости восстанавливаем комментарии.
                    EnsureCommentsPreserved(selectionSets, target, originalComments);

                    processedSets++;
                    renamedConditionsCount += changedInSet;
                }

                if (startedTransaction && transaction != null)
                {
                    transaction.Commit();
                }
            }
            finally
            {
                if (transaction != null)
                {
                    transaction.Dispose();
                }
            }

            return new RenameResult(unchanged, withoutSearch);
        }

        private static void Walk(GroupItem group, List<TargetSet> accumulate)
        {
            if (group == null)
            {
                return;
            }

            SavedItemCollection children = group.Children;
            if (children == null)
            {
                return;
            }

            for (int index = 0; index < children.Count; index++)
            {
                SavedItem child = children[index];
                if (child == null)
                {
                    continue;
                }

                GroupItem childGroup = child as GroupItem;
                if (childGroup != null)
                {
                    Walk(childGroup, accumulate);
                    continue;
                }

                // Лист — потенциальный поисковый набор.
                if (SearchSetRenameLogic.IsTarget(child.DisplayName))
                {
                    accumulate.Add(new TargetSet(child, group, index));
                }
            }
        }

        /// <summary>
        /// Строит новую последовательность условий поиска с переименованным свойством.
        /// Возвращает количество изменённых условий (0 — если менять было нечего).
        /// </summary>
        private static int BuildRenamedConditions(Search original, out IEnumerable<SearchCondition> renamedOutput)
        {
            renamedOutput = null;
            SearchConditionCollection source = original != null ? original.SearchConditions : null;
            if (source == null || source.Count == 0)
            {
                return 0;
            }

            var renamedList = new List<SearchCondition>(source.Count);
            int changes = 0;
            foreach (SearchCondition condition in source)
            {
                SearchCondition copy = RenameCondition(condition);
                if (copy != null)
                {
                    renamedList.Add(copy);
                    if (WasPropertyRenamed(condition, copy))
                    {
                        changes++;
                    }
                }
                else
                {
                    renamedList.Add(condition);
                }
            }

            renamedOutput = renamedList;
            return changes;
        }

        private static SearchCondition RenameCondition(SearchCondition source)
        {
            if (source == null)
            {
                return null;
            }

            // Сопоставление через NamedConstant.Name и NamedConstant.DisplayName,
            // без опоры на ToString() (формат не задокументирован в Navis 2024).
            // Категория сохраняется ссылкой на source.CategoryCombinedName.
            NamedConstant property = source.PropertyCombinedName;
            string renamedName = null;

            if (property != null)
            {
                if (SearchSetRenameLogic.GetRenamedProperty(property.Name) != null)
                {
                    renamedName = SearchSetRenameLogic.GetRenamedProperty(property.Name);
                }
                else if (SearchSetRenameLogic.GetRenamedProperty(property.DisplayName) != null)
                {
                    renamedName = SearchSetRenameLogic.GetRenamedProperty(property.DisplayName);
                }
            }

            if (renamedName == null)
            {
                return null;
            }

            NamedConstant newProperty = new NamedConstant(renamedName);

            // SearchCondition иммутабелен по свойствам. Создаём новый экземпляр, передавая
            // исходные категорию, опции, оператор сравнения и значение — они сохраняются.
            return new SearchCondition(
                source.CategoryCombinedName,
                newProperty,
                source.Options,
                source.Comparison,
                source.Value);
        }

        private static bool WasPropertyRenamed(SearchCondition original, SearchCondition copy)
        {
            if (original == null || copy == null)
            {
                return false;
            }
            return !SameNamedConstant(original.PropertyCombinedName, copy.PropertyCombinedName);
        }

        private static bool SameNamedConstant(NamedConstant left, NamedConstant right)
        {
            if (ReferenceEquals(left, right))
            {
                return true;
            }
            if (left == null || right == null)
            {
                return false;
            }
            if (left.Value != 0 && right.Value != 0 && left.Value == right.Value)
            {
                return true;
            }
            return string.Equals(left.Name, right.Name, StringComparison.Ordinal)
                || string.Equals(left.DisplayName, right.DisplayName, StringComparison.Ordinal)
                || string.Equals(left.BaseName, right.BaseName, StringComparison.Ordinal);
        }

        /// <summary>
        /// Гарантирует, что комментарии исходного набора перенесены в копию после замены
        /// в дереве. EditComments требует уже вставленный в дерево элемент, поэтому
        /// достаём его через parent.Children[index] (или selectionSets.Value[index] для
        /// верхнего уровня).
        /// </summary>
        private static void EnsureCommentsPreserved(
            DocumentSelectionSets selectionSets,
            TargetSet target,
            CommentCollection originalComments)
        {
            if (originalComments == null || originalComments.Count == 0)
            {
                return;
            }

            SavedItem inTree = null;
            try
            {
                if (target.ParentGroup == null)
                {
                    if (target.Index >= 0 && target.Index < selectionSets.Value.Count)
                    {
                        inTree = selectionSets.Value[target.Index];
                    }
                }
                else
                {
                    SavedItemCollection children = target.ParentGroup.Children;
                    if (children != null && target.Index >= 0 && target.Index < children.Count)
                    {
                        inTree = children[target.Index];
                    }
                }
            }
            catch
            {
                return;
            }

            if (inTree == null)
            {
                return;
            }

            bool same;
            try
            {
                CommentCollection current = inTree.Comments;
                same = CommentCollectionsEqual(current, originalComments);
            }
            catch
            {
                same = false;
            }

            if (!same)
            {
                selectionSets.EditComments(inTree, originalComments);
            }
        }

        private static bool CommentCollectionsEqual(CommentCollection a, CommentCollection b)
        {
            if (a == null || b == null)
            {
                return a == null && b == null;
            }
            if (a.Count != b.Count)
            {
                return false;
            }
            for (int i = 0; i < a.Count; i++)
            {
                Comment left = a[i];
                Comment right = b[i];
                if (left == null || right == null)
                {
                    if (left != right) return false;
                    continue;
                }
                if (!string.Equals(left.Body, right.Body, StringComparison.Ordinal)) return false;
                if (!string.Equals(left.Author, right.Author, StringComparison.Ordinal)) return false;
            }
            return true;
        }

        internal sealed class TargetSet
        {
            public TargetSet(SavedItem savedItem, GroupItem parent, int index)
            {
                SavedItem = savedItem;
                ParentGroup = parent;
                Index = index;
            }

            public SavedItem SavedItem { get; }
            public GroupItem ParentGroup { get; }
            public int Index { get; }
        }

        internal sealed class RenameResult
        {
            public RenameResult(int unchanged, int withoutSearch)
            {
                Unchanged = unchanged;
                WithoutSearch = withoutSearch;
            }

            public int Unchanged { get; }
            public int WithoutSearch { get; }
        }

        private static void Show(string message, MessageBoxIcon icon)
        {
            MessageBox.Show(message, "Переименование критериев поисковых наборов", MessageBoxButtons.OK, icon);
        }
    }
}
