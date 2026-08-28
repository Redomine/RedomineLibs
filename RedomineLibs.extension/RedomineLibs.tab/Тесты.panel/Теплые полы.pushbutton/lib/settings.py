# -*- coding: utf-8 -*-
import json
import os


DEFAULT_SETTINGS = {
    "step_mm": 150.0,
    "boundary_offset_mm": 75.0,
    "min_bend_radius_mm": 80.0,
    "max_loop_length_m": 100.0,
    "create_mode": "model",
    "allow_diagonal_transitions": False,
    "auto_exclude_unavailable_features": True,
    "show_diagnostics": True,
    "tolerance_mm": 2.0,
    "clipper_scale": 1000000.0,
    "max_offset_levels": 200
}


class SettingsStorage(object):
    def __init__(self, command_dir):
        self.command_dir = command_dir
        self.path = os.path.join(command_dir, "heating_snail_settings.json")

    def load(self):
        data = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as settings_file:
                    data = json.load(settings_file)
            except Exception:
                data = {}

        result = {}
        for key in DEFAULT_SETTINGS:
            result[key] = DEFAULT_SETTINGS[key]
        for key in data:
            if key in result:
                result[key] = data[key]
        return result

    def save(self, settings):
        data = {}
        for key in DEFAULT_SETTINGS:
            if key in settings:
                data[key] = settings[key]
        with open(self.path, "w") as settings_file:
            json.dump(data, settings_file, indent=4, sort_keys=True)
