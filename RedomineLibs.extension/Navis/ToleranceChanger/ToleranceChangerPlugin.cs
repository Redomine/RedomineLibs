using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Windows.Forms;
using Autodesk.Navisworks.Api;
using Autodesk.Navisworks.Api.Clash;
using Autodesk.Navisworks.Api.Plugins;

namespace Redomine.ToleranceChanger
{
    [Plugin("Redomine.ToleranceChanger", "RDMN", DisplayName = "Допуски проверок по этапу")]
    public sealed class ToleranceChangerPlugin : AddInPlugin
    {
        private static readonly string[] Sections = { "АУПТ", "ИТП", "ВК1 В", "ВК2 К", "ОВ1 О", "ОВ2 В" };
        private static readonly Dictionary<string, int?[]> Rules = new Dictionary<string, int?[]>(StringComparer.OrdinalIgnoreCase)
        {
            { "01|АР|ПРОЕЗД", new int?[] { null, 0, 0, 0 } },
            { "01|ВИС|", new int?[] { null, 100, 50, 10 } },
            { "01|SELF|", new int?[] { null, 100, 50, 10 } },
            { "01|SELF|ИЗОЛЯЦИЯ ИСКЛЮЧЕНА", new int?[] { null, 100, 50, 10 } },
            { "01|SELF|ТОЛЬКО ИЗОЛЯЦИЯ", new int?[] { null, 100, 50, 10 } },
            { "01|SELF|ДУБЛИРОВАНИЕ", new int?[] { 100, 100, 100, 100 } },
            { "01|КР|ПИЛОН", new int?[] { null, 10, 0, 0 } },
            { "02|АР|ЗАПОЛНЕНИЕ ПРОЕМО", new int?[] { null, 100, 50, 10 } },
            { "02|АР|КЛАДКА", new int?[] { null, 100, 50, 10 } },
            { "02|КР|МЕТАЛЛОКАРКАС", new int?[] { null, 10, 0, 0 } },
            { "02|КР|ПЕРЕКРЫТИЯ", new int?[] { null, 100, 50, 10 } }
        };

        public override int Execute(params string[] parameters)
        {
            Document document = Autodesk.Navisworks.Api.Application.ActiveDocument;
            if (document == null) { MessageBox.Show("Нет активного документа Navisworks."); return 1; }
            int stage;
            using (var dialog = new StageDialog())
            {
                if (dialog.ShowDialog() != DialogResult.OK) return 0;
                stage = dialog.Stage;
            }
            int changed = 0;
            var skipped = new List<string>();
            try
            {
                DocumentClashTests tests = document.GetClash().TestsData;
                foreach (ClashTest test in tests.Tests.OfType<ClashTest>().ToList())
                {
                    if (ShouldIgnore(test.DisplayName)) continue;
                    if (GetSection(test.DisplayName) == null) continue;
                    int? mm = FindTolerance(test.DisplayName, stage);
                    if (!mm.HasValue) { skipped.Add(test.DisplayName); continue; }
                    var copy = (ClashTest)test.CreateCopy();
                    copy.Tolerance = mm.Value * UnitConversion.ScaleFactor(Units.Millimeters, document.Units);
                    tests.TestsEditTestFromCopy(test, copy);
                    changed++;
                }
                MessageBox.Show("Этап " + stage + ". Обновлено: " + changed + ". Не распознано: " + skipped.Count +
                    (skipped.Count == 0 ? "" : "\n\n" + string.Join("\n", skipped.Take(20))), "ToleranceChanger");
                return 0;
            }
            catch (Exception error) { MessageBox.Show(error.Message, "ToleranceChanger"); return 1; }
        }

        private static bool ShouldIgnore(string name)
        {
            if (string.IsNullOrEmpty(name)) return false;
            return new[] { "АР-АР", "АР-КР", "КР-АР", "ЭОМ", "СС" }
                .Any(part => name.IndexOf(part, StringComparison.OrdinalIgnoreCase) >= 0);
        }

        private static int? FindTolerance(string name, int stage)
        {
            if (string.IsNullOrWhiteSpace(name) || name.Length < 4 || name[2] != '_') return null;
            string section = GetSection(name);
            if (section == null) return null;
            string rest = name.Substring(3 + section.Length + 1);
            int end = rest.IndexOfAny(new[] { '_', '(' });
            string other = (end < 0 ? rest : rest.Substring(0, end)).ToUpperInvariant();
            if (other == section.ToUpperInvariant()) other = "SELF";
            string detail = name.ToUpperInvariant();
            string prefix = name.Substring(0, 2) + "|" + other + "|";
            var candidates = Rules.Where(r => r.Key.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                .Where(r => { string suffix = r.Key.Substring(prefix.Length); return suffix.Length == 0 || detail.Contains(suffix); })
                .OrderByDescending(r => r.Key.Length).ToList();
            if (candidates.Count == 0) return null;
            int?[] values = candidates[0].Value;
            if (values[stage - 1].HasValue) return values[stage - 1];
            return Enumerable.Range(0, 4).Where(i => values[i].HasValue).OrderBy(i => Math.Abs(i - (stage - 1))).Select(i => values[i]).FirstOrDefault();
        }

        private static string GetSection(string name)
        {
            if (string.IsNullOrEmpty(name) || name.Length < 4 || name[2] != '_') return null;
            return Sections.FirstOrDefault(s => name.Substring(3).StartsWith(s + "-", StringComparison.OrdinalIgnoreCase));
        }

        private sealed class StageDialog : Form
        {
            private readonly ComboBox selection = new ComboBox();
            public int Stage { get { return selection.SelectedIndex + 1; } }
            public StageDialog()
            {
                Text = "Выбор этапа"; Width = 260; Height = 145; FormBorderStyle = FormBorderStyle.FixedDialog;
                StartPosition = FormStartPosition.CenterScreen; MaximizeBox = false; MinimizeBox = false;
                selection.DropDownStyle = ComboBoxStyle.DropDownList; selection.Left = 15; selection.Top = 15; selection.Width = 210;
                selection.Items.AddRange(new object[] { "Этап 1", "Этап 2", "Этап 3", "Этап 4" }); selection.SelectedIndex = 0;
                var ok = new Button { Text = "Применить", Left = 125, Top = 55, Width = 100, DialogResult = DialogResult.OK };
                Controls.Add(selection); Controls.Add(ok); AcceptButton = ok;
            }
        }
    }
}
