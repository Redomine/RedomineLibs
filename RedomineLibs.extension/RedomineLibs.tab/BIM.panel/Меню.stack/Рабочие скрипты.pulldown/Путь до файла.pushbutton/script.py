# -*- coding: utf-8 -*-

import clr

clr.AddReference("System.Windows.Forms")

from System.Windows.Forms import Clipboard
from Autodesk.Revit.DB import ModelPathUtils


document = __revit__.ActiveUIDocument.Document
path = document.PathName

if document.IsWorkshared and not document.IsFamilyDocument and not getattr(document, "IsDetached", False):
    central_path = document.GetWorksharingCentralModelPath()
    if central_path:
        path = ModelPathUtils.ConvertModelPathToUserVisiblePath(central_path)

if path:
    Clipboard.SetText(path)
