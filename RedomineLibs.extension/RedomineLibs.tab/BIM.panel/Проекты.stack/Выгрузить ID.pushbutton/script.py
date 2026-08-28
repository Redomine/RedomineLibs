# -*- coding: utf-8 -*-

import os
import clr

clr.AddReference("System.Windows.Forms")

from System.Windows.Forms import DialogResult, SaveFileDialog
from System.IO import StreamWriter
from System.Text import UTF8Encoding
from System.Runtime.InteropServices import Marshal

from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
from Autodesk.Revit.UI import TaskDialog


__title__ = "Выгрузить ID"
__doc__ = "Экспортирует категории и ID элементов из активного документа Revit в Excel."


try:
    text_type = unicode
except NameError:
    text_type = str


TARGET_CATEGORIES = [
    BuiltInCategory.OST_DuctFitting,
    BuiltInCategory.OST_PipeFitting,
    BuiltInCategory.OST_PipeCurves,
    BuiltInCategory.OST_DuctCurves,
    BuiltInCategory.OST_FlexDuctCurves,
    BuiltInCategory.OST_FlexPipeCurves,
    BuiltInCategory.OST_DuctTerminal,
    BuiltInCategory.OST_DuctAccessory,
    BuiltInCategory.OST_PipeAccessory,
    BuiltInCategory.OST_MechanicalEquipment,
    BuiltInCategory.OST_DuctInsulations,
    BuiltInCategory.OST_PipeInsulations,
    BuiltInCategory.OST_PlumbingFixtures,
    BuiltInCategory.OST_Sprinklers,
    BuiltInCategory.OST_CableTray,
]


def to_text(value):
    if value is None:
        return u""

    if isinstance(value, text_type):
        return value

    try:
        return text_type(value)
    except Exception:
        return text_type(str(value))


def show_message(message):
    TaskDialog.Show(__title__, message)


def get_active_document():
    uidoc = __revit__.ActiveUIDocument
    if uidoc is None:
        return None
    return uidoc.Document


def collect_rows(doc):
    rows = []

    for category_id in TARGET_CATEGORIES:
        collector = (
            FilteredElementCollector(doc)
            .OfCategory(category_id)
            .WhereElementIsNotElementType()
        )

        for element in collector:
            if element.Category is not None:
                category_name = to_text(element.Category.Name)
            else:
                category_name = to_text(category_id)

            rows.append((category_name, element.Id.IntegerValue))

    rows.sort(key=lambda row: (row[0], row[1]))
    return rows


def prompt_save_path():
    dialog = SaveFileDialog()
    dialog.Title = u"Сохранить список элементов"
    dialog.Filter = u"Книга Excel (*.xlsx)|*.xlsx|CSV (*.csv)|*.csv"
    dialog.FilterIndex = 1
    dialog.DefaultExt = "xlsx"
    dialog.AddExtension = True
    dialog.OverwritePrompt = True
    dialog.FileName = u"Список ID элементов.xlsx"

    if dialog.ShowDialog() != DialogResult.OK:
        return None

    return dialog.FileName


def csv_value(value):
    return u'"{0}"'.format(to_text(value).replace(u'"', u'""'))


def write_csv(save_path, rows):
    writer = StreamWriter(save_path, False, UTF8Encoding(True))

    try:
        writer.WriteLine(u"{0};{1}".format(csv_value(u"Категория"), csv_value(u"ID элемента")))

        for category_name, element_id in rows:
            writer.WriteLine(u"{0};{1}".format(csv_value(category_name), csv_value(element_id)))
    finally:
        writer.Close()


def release_com_object(com_object):
    if com_object is None:
        return

    try:
        Marshal.FinalReleaseComObject(com_object)
    except Exception:
        try:
            Marshal.ReleaseComObject(com_object)
        except Exception:
            pass


def write_xlsx(save_path, rows):
    clr.AddReference("Microsoft.Office.Interop.Excel")
    from Microsoft.Office.Interop import Excel

    excel_app = None
    workbooks = None
    workbook = None
    worksheet = None
    header_range = None

    try:
        try:
            excel_app = Excel.ApplicationClass()
        except Exception:
            excel_app = Excel.Application()

        excel_app.Visible = False
        excel_app.DisplayAlerts = False

        workbooks = excel_app.Workbooks
        workbook = workbooks.Add()
        worksheet = workbook.Worksheets[1]
        worksheet.Name = u"Элементы"

        worksheet.Cells[1, 1].Value2 = u"Категория"
        worksheet.Cells[1, 2].Value2 = u"ID элемента"

        for index, (category_name, element_id) in enumerate(rows, start=2):
            worksheet.Cells[index, 1].Value2 = category_name
            worksheet.Cells[index, 2].Value2 = element_id

        header_range = worksheet.Range[worksheet.Cells[1, 1], worksheet.Cells[1, 2]]
        header_range.Font.Bold = True
        worksheet.Columns[1].AutoFit()
        worksheet.Columns[2].AutoFit()

        workbook.SaveAs(save_path, Excel.XlFileFormat.xlOpenXMLWorkbook)
    finally:
        if workbook is not None:
            workbook.Close(False)

        if excel_app is not None:
            excel_app.Quit()

        release_com_object(header_range)
        release_com_object(worksheet)
        release_com_object(workbook)
        release_com_object(workbooks)
        release_com_object(excel_app)


def export_rows(save_path, rows):
    extension = os.path.splitext(save_path)[1].lower()

    if extension == ".csv":
        write_csv(save_path, rows)
        return save_path, u"csv", None

    try:
        write_xlsx(save_path, rows)
        return save_path, u"xlsx", None
    except Exception as export_error:
        fallback_path = os.path.splitext(save_path)[0] + ".csv"
        write_csv(fallback_path, rows)
        return fallback_path, u"csv_fallback", to_text(export_error)


def main():
    doc = get_active_document()
    if doc is None:
        show_message(u"Активный документ Revit не найден.")
        return

    rows = collect_rows(doc)
    save_path = prompt_save_path()

    if not save_path:
        return

    try:
        exported_path, export_mode, export_error = export_rows(save_path, rows)
    except Exception as save_error:
        show_message(u"Не удалось сохранить файл.\n\n{0}".format(to_text(save_error)))
        return

    if export_mode == u"csv_fallback":
        show_message(
            u"Excel-файл сохранить не удалось, поэтому данные выгружены в CSV.\n\n"
            u"Элементов: {0}\n"
            u"Файл: {1}\n\n"
            u"Причина: {2}".format(len(rows), exported_path, export_error)
        )
        return

    show_message(
        u"Выгрузка завершена.\n\n"
        u"Элементов: {0}\n"
        u"Файл: {1}".format(len(rows), exported_path)
    )


if __name__ == "__main__":
    main()
