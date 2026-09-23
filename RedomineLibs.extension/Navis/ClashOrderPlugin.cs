using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Windows.Forms;
using System.Xml;
using Autodesk.Navisworks.Api;
using Autodesk.Navisworks.Api.Clash;
using Autodesk.Navisworks.Api.Plugins;

namespace Redomine.NavisClashOrder
{
    [Plugin("Redomine.ClashOrder", "RDMN", DisplayName = "Порядок клэш-тестов из XML")]
    public sealed class ClashOrderPlugin : AddInPlugin
    {
        private const string XmlFileName = "Clash Detective.xml";

        public override int Execute(params string[] parameters)
        {
            try
            {
                string assemblyDirectory = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
                string xmlPath = Path.Combine(assemblyDirectory ?? string.Empty, XmlFileName);

                if (!File.Exists(xmlPath))
                {
                    Show("Не найден файл:\n" + xmlPath, MessageBoxIcon.Warning);
                    return 1;
                }

                Document document = Autodesk.Navisworks.Api.Application.ActiveDocument;
                if (document == null)
                {
                    Show("Нет активного документа Navisworks.", MessageBoxIcon.Warning);
                    return 1;
                }

                List<string> xmlNames = ReadTestNames(xmlPath);
                ReorderResult result = Reorder(document.GetClash().TestsData, xmlNames);

                Show(
                    "Порядок клэш-тестов обновлён.\n\n" +
                    "Совпало: " + result.MatchedCount + "\n" +
                    "Перемещено: " + result.MoveCount + "\n" +
                    "Нет в Navisworks: " + result.MissingInNavisworks + "\n" +
                    "Нет в XML: " + result.MissingInXml,
                    MessageBoxIcon.Information);
                return 0;
            }
            catch (Exception exception)
            {
                Show("Не удалось изменить порядок клэш-тестов.\n\n" + exception.Message, MessageBoxIcon.Error);
                return 1;
            }
        }

        private static List<string> ReadTestNames(string xmlPath)
        {
            var settings = new XmlReaderSettings
            {
                DtdProcessing = DtdProcessing.Prohibit,
                XmlResolver = null
            };

            var document = new XmlDocument { XmlResolver = null };
            using (XmlReader reader = XmlReader.Create(xmlPath, settings))
            {
                document.Load(reader);
            }

            var names = new List<string>();
            XmlNodeList nodes = document.SelectNodes("//*[local-name()='clashtest']");
            if (nodes == null)
            {
                return names;
            }

            foreach (XmlNode node in nodes)
            {
                XmlAttribute nameAttribute = node.Attributes?["name"];
                if (nameAttribute != null && !string.IsNullOrEmpty(nameAttribute.Value))
                {
                    names.Add(nameAttribute.Value);
                }
            }

            return names;
        }

        private static ReorderResult Reorder(DocumentClashTests clashTests, IList<string> xmlNames)
        {
            List<SavedItem> originalItems = clashTests.Tests.ToList();
            List<ClashTest> originalTests = originalItems.OfType<ClashTest>().ToList();
            var byName = originalTests
                .GroupBy(test => test.DisplayName ?? string.Empty, StringComparer.Ordinal)
                .ToDictionary(group => group.Key, group => new Queue<ClashTest>(group), StringComparer.Ordinal);

            var orderedMatches = new List<ClashTest>();
            int missingInNavisworks = 0;
            foreach (string name in xmlNames)
            {
                Queue<ClashTest> candidates;
                if (byName.TryGetValue(name, out candidates) && candidates.Count > 0)
                {
                    orderedMatches.Add(candidates.Dequeue());
                }
                else
                {
                    missingInNavisworks++;
                }
            }

            var matchedIds = new HashSet<Guid>(orderedMatches.Select(test => test.Guid));
            var desired = new Queue<ClashTest>(orderedMatches);
            var target = new List<SavedItem>(originalItems.Count);
            foreach (SavedItem item in originalItems)
            {
                var test = item as ClashTest;
                target.Add(test != null && matchedIds.Contains(test.Guid) ? desired.Dequeue() : item);
            }

            int moveCount = 0;
            for (int targetIndex = 0; targetIndex < target.Count; targetIndex++)
            {
                List<SavedItem> current = clashTests.Tests.ToList();
                if (current[targetIndex].Guid == target[targetIndex].Guid)
                {
                    continue;
                }

                int sourceIndex = current.FindIndex(targetIndex + 1, item => item.Guid == target[targetIndex].Guid);
                if (sourceIndex < 0)
                {
                    throw new InvalidOperationException("Состав клэш-тестов изменился во время выполнения команды.");
                }

                clashTests.TestsMove(sourceIndex, targetIndex);
                moveCount++;
            }

            return new ReorderResult(
                orderedMatches.Count,
                moveCount,
                missingInNavisworks,
                originalTests.Count - orderedMatches.Count);
        }

        private static void Show(string message, MessageBoxIcon icon)
        {
            MessageBox.Show(message, "Порядок клэш-тестов", MessageBoxButtons.OK, icon);
        }

        private sealed class ReorderResult
        {
            public ReorderResult(int matchedCount, int moveCount, int missingInNavisworks, int missingInXml)
            {
                MatchedCount = matchedCount;
                MoveCount = moveCount;
                MissingInNavisworks = missingInNavisworks;
                MissingInXml = missingInXml;
            }

            public int MatchedCount { get; }
            public int MoveCount { get; }
            public int MissingInNavisworks { get; }
            public int MissingInXml { get; }
        }
    }
}
