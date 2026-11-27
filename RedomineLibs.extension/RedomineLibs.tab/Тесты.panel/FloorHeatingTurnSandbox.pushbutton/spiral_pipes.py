# -*- coding: utf-8 -*-
"""Materialise a calculated spiral in the caller's Revit transaction."""
from __future__ import division

import math
from collections import namedtuple

import clr
import dosymep
from System import Guid
from Autodesk.Revit.DB.ExtensibleStorage import AccessLevel, Entity, Schema, SchemaBuilder
from Autodesk.Revit.DB.Plumbing import Pipe
from Autodesk.Revit.Exceptions import RegenerationFailedException
from pyrevit import DB

from build_progress import checkpoint, report_model_step
from floor_heating_geometry import TOLERANCE

clr.ImportExtensions(dosymep.Revit)

# Pipe.Create requires at least 1/10 inch. The solver uses millimetres.
MIN_PIPE_MM = 25.4 / 10
MM_PER_FOOT = 304.8
POSITION_TOL = TOLERANCE / MM_PER_FOOT
ROUTE_SCHEMA_ID = Guid("720c6507-27c6-4149-bce2-c36bddcb176e")
Run = namedtuple("Run", "part profile start end")
Placed = namedtuple("Placed", "run pipe start_id end_id")


def route_schema():
    return Schema.Lookup(ROUTE_SCHEMA_ID)


def _region_marker(region_id):
    schema = route_schema()
    if schema is None:
        builder = SchemaBuilder(ROUTE_SCHEMA_ID)
        builder.SetSchemaName("RedomineFloorHeatingRoute")
        builder.SetReadAccessLevel(AccessLevel.Public)
        builder.SetWriteAccessLevel(AccessLevel.Public)
        builder.AddSimpleField("SourceRegion", clr.GetClrType(DB.ElementId))
        schema = builder.Finish()
    marker = Entity(schema)
    marker.Set[DB.ElementId]("SourceRegion", region_id)
    return marker


def _system_type(pipe):
    param = pipe.get_Parameter(DB.BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
    type_id = param.AsElementId() if param is not None else None
    if type_id is not None and type_id != DB.ElementId.InvalidElementId:
        return type_id
    system = pipe.MEPSystem
    if system is not None:
        return system.GetTypeId()
    raise ValueError(u"У трубы {0} отсутствует тип системы.".format(pipe.Id.GetIdValue()))


def _level_id(pipe):
    level = pipe.ReferenceLevel
    if level is not None:
        return level.Id
    param = pipe.get_Parameter(DB.BuiltInParameter.RBS_START_LEVEL_PARAM)
    level_id = param.AsElementId() if param is not None else None
    if level_id is None or level_id == DB.ElementId.InvalidElementId:
        raise ValueError(u"У трубы {0} отсутствует опорный уровень.".format(pipe.Id.GetIdValue()))
    return level_id


def _workset(element):
    return element.get_Parameter(DB.BuiltInParameter.ELEM_PARTITION_PARAM)


class PipeProfile(object):
    def __init__(self, document, end):
        source = document.GetElement(end["element_id"])
        self.pipe_type = source.GetTypeId()
        self.system_type = _system_type(source)
        self.level = _level_id(source)
        self.z = end["elevation_ft"]
        self.diameter = source.Diameter
        self.created = source.CreatedPhaseId
        self.demolished = source.DemolishedPhaseId
        self.workset = _workset(source).AsInteger() if document.IsWorkshared else None

    def assign(self, element):
        if element.CreatedPhaseId != self.created:
            element.CreatedPhaseId = self.created
        if element.DemolishedPhaseId != self.demolished:
            element.DemolishedPhaseId = self.demolished
        if self.workset is not None:
            param = _workset(element)
            if param.AsInteger() != self.workset:
                param.Set(self.workset)

    def check_phase_workset(self, element):
        if (element.CreatedPhaseId != self.created or element.DemolishedPhaseId != self.demolished or
                (self.workset is not None and _workset(element).AsInteger() != self.workset)):
            raise ValueError(u"Revit изменил стадию или рабочий набор элемента {0}.".format(element.Id.GetIdValue()))


def _near(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= TOLERANCE


def _runs(branches, join, profiles, first_side, lift, minimum):
    second_side = "right" if first_side == "left" else "left"
    paths = {side: branches[side]["vertices"] for side in (first_side, second_side)}
    if not paths[first_side] or not paths[second_side]:
        raise ValueError(u"Отсутствуют вершины ветви улитки.")
    a, b = paths[first_side][-1], paths[second_side][-1]
    bridge = join["bridge"]
    if bridge is None:
        matched = _near(a, b)
    else:
        x, y = bridge
        matched = (_near(a, x) and _near(b, y)) or (_near(a, y) and _near(b, x))
    if not matched:
        raise ValueError(u"Перемычка не совпадает с концами обеих ветвей.")

    points = {side: [lift(p, profiles[side].z) for p in path] for side, path in paths.items()}
    sections = [(first_side, first_side, points[first_side]),
                ("bridge", first_side, [points[first_side][-1], points[second_side][-1]]),
                (second_side, second_side, list(reversed(points[second_side])))]
    runs = []
    for part, side, vertices in sections:
        for start, end in zip(vertices, vertices[1:]):
            distance = start.DistanceTo(end)
            if distance <= POSITION_TOL:
                continue
            if distance < minimum:
                raise ValueError(u"Участок трубы длиной {0:.2f} мм короче допустимого.".format(distance * MM_PER_FOOT))
            runs.append(Run(part, profiles[side], start, end))
    if not runs:
        raise ValueError(u"В маршруте нет участков для построения труб.")
    return runs


def _place(document, run, marker):
    profile = run.profile
    pipe = Pipe.Create(document, profile.system_type, profile.pipe_type, profile.level, run.start, run.end)
    diameter = pipe.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    if abs(diameter.AsDouble() - profile.diameter) > POSITION_TOL:
        diameter.Set(profile.diameter)
    profile.assign(pipe)
    pipe.SetEntity(marker)
    return pipe


def _ends(pipe, run):
    ends = [c for c in pipe.ConnectorManager.Connectors if c.ConnectorType == DB.ConnectorType.End]
    if len(ends) != 2:
        raise ValueError(u"У созданной трубы ожидаются два концевых соединителя.")
    a, b = sorted(ends, key=lambda c: c.Origin.DistanceTo(run.start))
    if a.Origin.DistanceTo(run.start) > POSITION_TOL or b.Origin.DistanceTo(run.end) > POSITION_TOL:
        raise ValueError(u"Концы созданной трубы не совпадают с расчётными точками.")
    return Placed(run, pipe, a.Id, b.Id)


def _joins_ends(a, b, fitting):
    ends = [c for c in fitting.MEPModel.ConnectorManager.Connectors if c.ConnectorType == DB.ConnectorType.End]
    return len(ends) == 2 and ((a.IsConnectedTo(ends[0]) and b.IsConnectedTo(ends[1])) or
                               (a.IsConnectedTo(ends[1]) and b.IsConnectedTo(ends[0])))


def _fit(document, before, after):
    # An elbow can shorten a pipe. Reacquire its other end by connector ID.
    a = before.pipe.ConnectorManager.Lookup(before.end_id)
    b = after.pipe.ConnectorManager.Lookup(after.start_id)
    if a.IsConnected or b.IsConnected:
        raise ValueError(u"Конец новой трубы уже занят перед установкой фитинга.")
    if a.CoordinateSystem.BasisZ.IsAlmostEqualTo(b.CoordinateSystem.BasisZ.Negate()):
        if abs(a.Radius - b.Radius) > POSITION_TOL:
            create, name = document.Create.NewTransitionFitting, u"переход"
        else:
            create, name = document.Create.NewUnionFitting, u"муфту"
    else:
        create, name = document.Create.NewElbowFitting, u"отвод"
    try:
        fitting = create(a, b)
        # NewUnionFitting may place a substitute and return null. Accept only
        # the fitting physically attached to both previously free pipe ends.
        if fitting is None:
            document.Regenerate()
            a = before.pipe.ConnectorManager.Lookup(before.end_id)
            b = after.pipe.ConnectorManager.Lookup(after.start_id)
            fitting = next((ref.Owner for ref in a.AllRefs if ref.ConnectorType == DB.ConnectorType.End
                            and isinstance(ref.Owner, DB.FamilyInstance) and _joins_ends(a, b, ref.Owner)), None)
            if fitting is None:
                raise ValueError(u"Между концами труб отсутствует фитинг.")
    except RegenerationFailedException:
        raise
    except Exception as error:
        raise ValueError(u"Не удалось установить {0}. Типы труб на стыке {1} и {2}. "
                         u"Проверьте настройки трассировки этих типов и место для фитинга. {3}".format(
                             name, before.run.profile.pipe_type.GetIdValue(), after.run.profile.pipe_type.GetIdValue(), error))
    before.run.profile.assign(fitting)
    return fitting


def _verify_chain(placed, fittings):
    for item in placed:
        checkpoint()
        pipe, run = item.pipe, item.run
        profile = run.profile
        profile.check_phase_workset(pipe)
        if (pipe.GetTypeId() != profile.pipe_type or _system_type(pipe) != profile.system_type or
                _level_id(pipe) != profile.level or abs(pipe.Diameter - profile.diameter) > POSITION_TOL):
            raise ValueError(u"После соединения Revit изменил тип трубы, тип системы, уровень или диаметр "
                             u"участка {0}.".format(pipe.Id.GetIdValue()))
        direction = (run.end - run.start).Normalize()
        curve = pipe.Location.Curve
        for i in (0, 1):
            if (curve.GetEndPoint(i) - run.start).CrossProduct(direction).GetLength() > POSITION_TOL:
                raise ValueError(u"Фитинг сместил ось трубы с расчётной линии или отметки.")
    for before, after, fitting in zip(placed, placed[1:], fittings):
        before.run.profile.check_phase_workset(fitting)
        first = before.pipe.ConnectorManager.Lookup(before.end_id)
        second = after.pipe.ConnectorManager.Lookup(after.start_id)
        if not _joins_ends(first, second, fitting):
            raise ValueError(u"Фитинг {0} не соединил обе соседние трубы.".format(fitting.Id.GetIdValue()))
    for item, index, point in ((placed[0], placed[0].start_id, placed[0].run.start),
                               (placed[-1], placed[-1].end_id, placed[-1].run.end)):
        inlet = item.pipe.ConnectorManager.Lookup(index)
        if inlet.IsConnected or inlet.Origin.DistanceTo(point) > POSITION_TOL:
            raise ValueError(u"Вход улитки должен оставаться свободным в расчётной точке.")


def build_pipes(document, region_id, branches, join, ends, first_side, lift, max_length_m):
    pipe_ids = {"right": [], "left": [], "bridge": []}
    if not join["connected"]:
        return pipe_ids, []
    profiles = {side: PipeProfile(document, end) for side, end in ends.items()}
    minimum = max(document.Application.ShortCurveTolerance, MIN_PIPE_MM / MM_PER_FOOT)
    runs = _runs(branches, join, profiles, first_side, lift, minimum)
    length_mm = sum(r.start.DistanceTo(r.end) for r in runs) * MM_PER_FOOT
    if length_mm > max_length_m * 1000 + TOLERANCE:
        raise ValueError(u"С учётом отметок длина трассы {0:.3f} м превышает предел {1:g} м.".format(
            length_mm / 1000, max_length_m))
    marker = _region_marker(region_id)
    pipes = []
    steps = 2 * len(runs) - 1
    for index, run in enumerate(runs):
        report_model_step(u"Построение труб", index, steps)
        pipes.append(_place(document, run, marker))
    document.Regenerate()
    placed = [_ends(pipe, run) for pipe, run in zip(pipes, runs)]
    fittings = []
    for index, (before, after) in enumerate(zip(placed, placed[1:])):
        report_model_step(u"Установка фитингов", len(pipes) + index, steps)
        fittings.append(_fit(document, before, after))
    report_model_step(u"Проверка соединений", steps, steps)
    document.Regenerate()
    _verify_chain(placed, fittings)
    for item in placed:
        pipe_ids[item.run.part].append(item.pipe.Id.GetIdValue())
    # Both journal lists run from their inlet towards the bridge.
    pipe_ids["left" if first_side == "right" else "right"].reverse()
    return pipe_ids, [f.Id.GetIdValue() for f in fittings]
