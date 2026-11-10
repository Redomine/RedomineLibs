# -*- coding: utf-8 -*-
from __future__ import print_function

import io
import os
import re
import shutil
import tempfile
import urllib
import uuid

import clr
clr.AddReference('Microsoft.Office.Interop.Excel')
from Microsoft.Office.Interop import Excel
from System import Type
from pyrevit import forms

__title__ = u'Подготовить сводный отчет'
__doc__ = u'Собрать HTML-отчеты по разделам в отдельные книги Excel.'

IMAGE_RE = re.compile(r'<img\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']', re.I)
GROUP_RE = re.compile(u'^\\d+_(ОВ|ВК|АУПТ|ИТП|СС|ЭОМ)')
GROUPS = (u'ОВ', u'ВК и АУПТ', u'ИТП', u'СС', u'ЭОМ')


def group_for(name):
    match = GROUP_RE.match(name)
    if not match:
        raise ValueError(u'Не определена группа отчета: ' + name)
    section = match.group(1)
    return u'ВК и АУПТ' if section in (u'ВК', u'АУПТ') else section


def sheet_name(name, used):
    base = re.sub(u'[\\[\\]:*?/\\\\]', u'_', os.path.splitext(name)[0])
    candidate = base[:31]
    index = 2
    while candidate.lower() in used:
        suffix = u' ({0})'.format(index)
        candidate = base[:31 - len(suffix)] + suffix
        index += 1
    used.add(candidate.lower())
    return candidate


def embed_images(sheet, html_path):
    with io.open(html_path, 'r', encoding='utf-8') as stream:
        html = stream.read()
    sources = IMAGE_RE.findall(html)
    pictures = [shape for shape in sheet.Shapes if shape.Type == 11]
    if len(sources) != len(pictures):
        raise ValueError(u'Не совпадает число рисунков: ' + html_path)
    for source, old in zip(sources, pictures):
        relative = urllib.unquote(source.encode('utf-8')).decode('utf-8')
        image_path = os.path.join(os.path.dirname(html_path),
                                  relative.replace('/', os.sep).replace('\\', os.sep))
        if not os.path.isfile(image_path):
            raise IOError(u'Не найден рисунок: ' + image_path)
        sheet.Shapes.AddPicture(image_path, False, True,
                                old.Left, old.Top, old.Width, old.Height)
        old.Delete()


def main():
    folder = forms.pick_folder(title=u'Выберите папку с HTML-отчетами')
    if not folder:
        return
    grouped = dict((group, []) for group in GROUPS)
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith('.html') and os.path.isfile(os.path.join(folder, name)):
            grouped[group_for(name)].append(name)
    total = sum(len(names) for names in grouped.values())
    if not total:
        forms.alert(u'В выбранной папке нет HTML-файлов.', title=__title__)
        return

    excel = None
    report = None
    source = None
    outputs = []
    completed = 0
    try:
        excel = Excel.ApplicationClass()
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.AskToUpdateLinks = False
        with forms.ProgressBar(title=u'Сборка отчетов: {value} из {max_value}',
                               cancellable=False) as progress:
            progress.update_progress(0, total)
            for group in GROUPS:
                names = grouped[group]
                if not names:
                    continue
                report = excel.Workbooks.Add()
                initial = [sheet for sheet in report.Worksheets]
                used = set()
                for name in names:
                    path = os.path.join(folder, name)
                    source = excel.Workbooks.Open(path, 0, True)
                    source.Worksheets[1].Copy(Type.Missing, report.Worksheets[report.Worksheets.Count])
                    copied = report.Worksheets[report.Worksheets.Count]
                    embed_images(copied, path)
                    copied.Name = sheet_name(name, used)
                    copied.Cells[1, 1].AddComment(u'Исходный файл: ' + name)
                    source.Close(False)
                    source = None
                    completed += 1
                    progress.update_progress(completed, total)
                for sheet in initial:
                    sheet.Delete()
                output = os.path.join(folder, u'Сводный отчет - ' + group + u'.xlsx')
                local_output = os.path.join(tempfile.gettempdir(),
                                            'revit_report_' + uuid.uuid4().hex + '.xlsx')
                try:
                    report.SaveAs(local_output, 51)
                    report.Close(False)
                    report = None
                    if not os.path.isfile(local_output) or os.path.getsize(local_output) == 0:
                        raise IOError(u'Excel не создал книгу для группы ' + group)
                    shutil.copyfile(local_output, output)
                except Exception as error:
                    raise IOError(u'Не удалось сохранить группу {0} в {1}: {2}'.format(
                        group, output, error))
                finally:
                    if os.path.isfile(local_output):
                        os.remove(local_output)
                outputs.append(output)
        forms.alert(u'Готово. Создано книг: {0}. Обработано HTML-файлов: {1}.'.format(
            len(outputs), total), title=__title__)
    except Exception as error:
        forms.alert(u'Ошибка после обработки {0} файлов:\n{1}'.format(
            completed, error), title=__title__)
        raise
    finally:
        if source is not None:
            source.Close(False)
        if report is not None:
            report.Close(False)
        if excel is not None:
            excel.Quit()


if __name__ == '__main__':
    main()
