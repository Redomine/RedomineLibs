# -*- coding: utf-8 -*-
import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import CurveElement
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException


class RevitSelectionException(Exception):
    pass


class CurveElementSelectionFilter(ISelectionFilter):
    def AllowElement(self, element):
        try:
            if not isinstance(element, CurveElement):
                return False
            curve = element.GeometryCurve
            if curve is None:
                return False
            return curve.GetType().Name == "Line"
        except Exception:
            return False

    def AllowReference(self, reference, position):
        return True


class RevitSelectionService(object):
    """Selection and Revit curve extraction for ModelLine and DetailLine."""

    def __init__(self, uidoc, doc):
        self.uidoc = uidoc
        self.doc = doc
        self.curve_filter = CurveElementSelectionFilter()

    def pick_boundary_curves(self):
        try:
            references = self.uidoc.Selection.PickObjects(
                ObjectType.Element,
                self.curve_filter,
                "Выберите замкнутую цепочку ModelLine/DetailLine для границы теплого пола"
            )
        except OperationCanceledException:
            raise
        except Exception as exc:
            raise RevitSelectionException("Ошибка выбора границы: {0}".format(exc))

        elements = []
        curves = []
        for reference in references:
            element = self.doc.GetElement(reference.ElementId)
            curve = self.get_curve(element)
            elements.append(element)
            curves.append(curve)

        if len(curves) < 4:
            raise RevitSelectionException("Для границы нужно выбрать минимум 4 линии.")
        return elements, curves

    def pick_single_curve(self, prompt):
        try:
            reference = self.uidoc.Selection.PickObject(ObjectType.Element, self.curve_filter, prompt)
        except OperationCanceledException:
            raise
        except Exception as exc:
            raise RevitSelectionException("Ошибка выбора линии: {0}".format(exc))

        element = self.doc.GetElement(reference.ElementId)
        return element, self.get_curve(element)

    def get_curve(self, element):
        if element is None:
            raise RevitSelectionException("Не найден выбранный элемент.")
        if not isinstance(element, CurveElement):
            raise RevitSelectionException("Элемент {0} не является ModelLine/DetailLine.".format(element.Id))

        curve = element.GeometryCurve
        if curve is None:
            raise RevitSelectionException("У элемента {0} нет GeometryCurve.".format(element.Id))
        if curve.GetType().Name != "Line":
            raise RevitSelectionException("Этап 1 поддерживает только прямые линии. Элемент: {0}.".format(element.Id))
        return curve
