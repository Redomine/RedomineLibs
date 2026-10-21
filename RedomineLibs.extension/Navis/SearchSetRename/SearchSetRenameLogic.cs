using System;
using System.Collections.Generic;

namespace Redomine.NavisSearchSetRename
{
    /// <summary>
    /// Чистая логика отбора и переименования критериев поисковых наборов Navisworks.
    /// Не зависит от типов Navis API — легко покрывается unit-тестами.
    /// </summary>
    internal static class SearchSetRenameLogic
    {
        /// <summary>
        /// Префиксы имён поисковых наборов, которые нужно обработать.
        /// Имя набора должно начинаться с одного из них (ordinal ignore-case).
        /// </summary>
        public static readonly IReadOnlyList<string> TargetPrefixes = new[]
        {
            "ВК1 В",
            "ВК2 К",
        };

        /// <summary>
        /// Список замен свойств. Сопоставление по точному имени (ordinal ignore-case).
        /// Проверяется как отдельное имя, так и комбинированное с префиксом категории.
        /// </summary>
        public static readonly IReadOnlyList<PropertyRename> PropertyRenames = new[]
        {
            new PropertyRename("ФОП_ВИС_Имя системы", "Имя системы"),
        };

        /// <summary>
        /// Возвращает true, если имя поискового набора попадает под обработку.
        /// </summary>
        public static bool IsTarget(string displayName)
        {
            if (string.IsNullOrEmpty(displayName))
            {
                return false;
            }

            foreach (string prefix in TargetPrefixes)
            {
                if (displayName.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        /// <summary>
        /// Возвращает новое имя свойства, если для данного имени есть замена; иначе null.
        /// Проверяется обычное имя и комбинированная форма "&lt;категория&gt;::&lt;имя_свойства&gt;".
        /// Регистр игнорируется, формат ToString() у NamedConstant не используется.
        /// </summary>
        public static string GetRenamedProperty(string currentPropertyName)
        {
            if (string.IsNullOrEmpty(currentPropertyName))
            {
                return null;
            }

            foreach (PropertyRename rename in PropertyRenames)
            {
                if (string.Equals(currentPropertyName, rename.OldProperty, StringComparison.OrdinalIgnoreCase))
                {
                    return rename.NewProperty;
                }

                const string separator = "::";
                if (currentPropertyName.Length >= separator.Length + rename.OldProperty.Length &&
                    currentPropertyName.EndsWith(separator + rename.OldProperty, StringComparison.OrdinalIgnoreCase))
                {
                    int prefixLength = currentPropertyName.Length - separator.Length - rename.OldProperty.Length;
                    string category = currentPropertyName.Substring(0, prefixLength);
                    return string.IsNullOrEmpty(category)
                        ? separator + rename.NewProperty
                        : category + separator + rename.NewProperty;
                }
            }
            return null;
        }

        public readonly struct PropertyRename
        {
            public PropertyRename(string oldProperty, string newProperty)
            {
                OldProperty = oldProperty;
                NewProperty = newProperty;
            }

            public string OldProperty { get; }
            public string NewProperty { get; }
        }
    }
}
