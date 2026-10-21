using System;
using Redomine.NavisSearchSetRename;
using static Redomine.NavisSearchSetRename.Tests.Program;

namespace Redomine.NavisSearchSetRename.Tests
{
    /// <summary>
    /// Тесты чистой логики отбора и переименования критериев в SearchSetRenameLogic.
    /// </summary>
    internal sealed class SearchSetRenameLogicTests
    {
        [Test]
        public void IsTarget_MatchesVk1Prefix()
        {
            Assert.IsTrue(SearchSetRenameLogic.IsTarget("ВК1 В-что-то"));
            Assert.IsTrue(SearchSetRenameLogic.IsTarget("ВК1 В"));
        }

        [Test]
        public void IsTarget_MatchesVk2Prefix()
        {
            Assert.IsTrue(SearchSetRenameLogic.IsTarget("ВК2 К-АР_Кладка"));
            Assert.IsTrue(SearchSetRenameLogic.IsTarget("ВК2 К"));
        }

        [Test]
        public void IsTarget_RejectsUnrelatedNames()
        {
            Assert.IsFalse(SearchSetRenameLogic.IsTarget("АР-АР_(Кладка)"));
            Assert.IsFalse(SearchSetRenameLogic.IsTarget("01_ВК1 Ч-что-то"));
            Assert.IsFalse(SearchSetRenameLogic.IsTarget("01_ВК 1 В-что-то"));
        }

        [Test]
        public void IsTarget_RejectsNullAndEmpty()
        {
            Assert.IsFalse(SearchSetRenameLogic.IsTarget(null));
            Assert.IsFalse(SearchSetRenameLogic.IsTarget(string.Empty));
        }

        [Test]
        public void GetRenamedProperty_ReturnsReplacement()
        {
            Assert.AreEqual(
                "Имя системы",
                SearchSetRenameLogic.GetRenamedProperty("ФОП_ВИС_Имя системы"));
        }

        [Test]
        public void GetRenamedProperty_IgnoresCase()
        {
            Assert.AreEqual(
                "Имя системы",
                SearchSetRenameLogic.GetRenamedProperty("фоп_вис_имя СИСТЕМЫ"));
        }

        [Test]
        public void GetRenamedProperty_ReturnsNullForUnknown()
        {
            Assert.IsNull(SearchSetRenameLogic.GetRenamedProperty("Просто свойство"));
            Assert.IsNull(SearchSetRenameLogic.GetRenamedProperty(string.Empty));
            Assert.IsNull(SearchSetRenameLogic.GetRenamedProperty(null));
        }

        [Test]
        public void GetRenamedProperty_HandlesCombinedFormKeepsCategory()
        {
            Assert.AreEqual(
                "PGS::Имя системы",
                SearchSetRenameLogic.GetRenamedProperty("PGS::ФОП_ВИС_Имя системы"));
        }

        [Test]
        public void GetRenamedProperty_HandlesEmptyCategory()
        {
            Assert.AreEqual(
                "::Имя системы",
                SearchSetRenameLogic.GetRenamedProperty("::ФОП_ВИС_Имя системы"));
        }
    }

    internal static class Assert
    {
        public static void IsTrue(bool condition, string message = null)
        {
            if (!condition)
            {
                throw new AssertionException(message ?? "ожидалось true");
            }
        }

        public static void IsFalse(bool condition, string message = null)
        {
            if (condition)
            {
                throw new AssertionException(message ?? "ожидалось false");
            }
        }

        public static void IsNull(object value, string message = null)
        {
            if (value != null)
            {
                throw new AssertionException(message ?? "ожидалось null, получено: " + value);
            }
        }

        public static void AreEqual(object expected, object actual, string message = null)
        {
            if (!Equals(expected, actual))
            {
                throw new AssertionException(
                    message ?? "ожидалось '" + expected + "', получено '" + actual + "'");
            }
        }

        private sealed class AssertionException : Exception
        {
            public AssertionException(string message) : base(message) { }
        }
    }
}
