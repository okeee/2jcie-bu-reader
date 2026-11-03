#!/usr/bin/env python3
"""2JCIE-BU 用の Lagoon 3 ダッシュボード JSON を生成する。
ターゲットの形式は try.soracom.io/lagoon-gallery のテンプレート (harvest-backend-datasource) に合わせている。
IMSI はインポート時に入力する (__inputs の constant)。

使い方: python3 tools/gen_lagoon_dashboard.py [出力先 (既定: lagoon/dashboard.json)]
"""
import json
import sys

IMSI = "${VAR_IMSI}"
OUT = sys.argv[1] if len(sys.argv) > 1 else "lagoon/dashboard.json"
DS = {"type": "harvest-backend-datasource", "uid": "${DS_HARVEST}"}


def target(prop, ref):
    return {"datasource": DS, "datatype": "standard", "devicetype": "subscribers", "pro": True,
            "properties": prop, "refId": ref, "target": IMSI, "type": "timeseries"}


def thresholds(steps):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in steps]}


def stat(pid, title, prop, unit, x, y, steps, decimals=1, w=3, h=4):
    return {
        "id": pid, "type": "stat", "title": title, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "color": {"mode": "thresholds"},
                                     "thresholds": thresholds(steps)}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "value", "graphMode": "area", "justifyMode": "center", "textMode": "value",
                    "orientation": "auto"},
        "targets": [target(prop, "A")],
    }


def series(pid, title, props, x, y, w=12, h=8, overrides=None, unit=None, axis=None):
    defaults = {"unit": unit or "none", "color": {"mode": "palette-classic"},
                "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10,
                           "showPoints": "never", "spanNulls": False}}
    if axis:
        defaults["min"], defaults["max"] = axis
    return {
        "id": pid, "type": "timeseries", "title": title, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "none"}},
        "targets": [target(p, chr(65 + i)) for i, p in enumerate(props)],
    }


def dashed(name):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
                           {"id": "custom.fillOpacity", "value": 0}]}


def right_axis(name, unit):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "custom.axisPlacement", "value": "right"}, {"id": "unit", "value": unit}]}


G, Y, O, R = "green", "yellow", "orange", "red"
panels = [
    stat(1, "温度", "temperature", "celsius", 0, 0, [(None, "blue"), (18, G), (28, O), (31, R)]),
    stat(2, "湿度", "humidity", "percent", 3, 0, [(None, O), (40, G), (60, Y), (70, O)]),
    stat(3, "CO2 (eCO2)", "eco2", "ppm", 6, 0, [(None, G), (1000, Y), (1500, O), (2500, R)], decimals=0),
    stat(4, "熱中症指標 (WBGT)", "heat_stroke", "celsius", 9, 0, [(None, G), (21, Y), (25, O), (28, R)]),
    stat(5, "不快指数", "discomfort_index", "none", 12, 0, [(None, "blue"), (60, G), (75, Y), (80, O)]),
    stat(6, "騒音", "noise", "dB", 15, 0, [(None, G), (60, Y), (70, O)]),
    stat(7, "照度", "light", "lux", 18, 0, [(None, "text")], decimals=0),
    stat(8, "気圧", "pressure", "pressurehpa", 21, 0, [(None, "text")]),
    series(9, "温度・湿度", ["temperature", "humidity"], 0, 4, unit="celsius",
           overrides=[right_axis("humidity", "percent")]),
    series(10, "CO2・TVOC", ["eco2", "etvoc"], 12, 4, unit="ppm",
           overrides=[right_axis("etvoc", "ppb")]),
    series(11, "熱中症指標・不快指数", ["heat_stroke", "discomfort_index"], 0, 12, unit="celsius",
           overrides=[right_axis("discomfort_index", "none")]),
    series(12, "騒音 (1 分平均・最大)", ["noise", "noise_max"], 12, 12, unit="dB",
           overrides=[dashed("noise_max")]),
    series(13, "照度 (1 分平均・最大)", ["light", "light_max"], 0, 20, unit="lux",
           overrides=[dashed("light_max")]),
    series(14, "気圧", ["pressure"], 12, 20, unit="pressurehpa"),
    series(15, "震度相当値 (1 分最大)", ["seismic_intensity"], 0, 28, axis=(0, 7)),
    series(16, "BLE 受信件数 (件/分, 正常時 60)", ["samples"], 12, 28, axis=(0, 65),
           overrides=[{"matcher": {"id": "byName", "options": "samples"},
                       "properties": [{"id": "custom.drawStyle", "value": "bars"},
                                      {"id": "custom.fillOpacity", "value": 60}]}]),
]

dashboard = {
    "__inputs": [{"name": "DS_HARVEST", "label": "Soracom", "description": "", "type": "datasource",
                  "pluginId": "harvest-backend-datasource", "pluginName": "soracom-backend"},
                 {"name": "VAR_IMSI", "label": "IMSI", "description": "Harvest Data に送信している SIM の IMSI",
                  "type": "constant", "value": ""}],
    "__elements": {},
    "__requires": [
        {"type": "grafana", "id": "grafana", "name": "Grafana", "version": "9.3.9"},
        {"type": "datasource", "id": "harvest-backend-datasource", "name": "soracom-backend", "version": "1.0.0"},
        {"type": "panel", "id": "stat", "name": "Stat", "version": ""},
        {"type": "panel", "id": "timeseries", "name": "Time series", "version": ""},
    ],
    "annotations": {"list": []},
    "editable": True,
    "graphTooltip": 1,
    "links": [],
    "panels": panels,
    "refresh": "1m",
    "schemaVersion": 37,
    "tags": ["2JCIE-BU"],
    "templating": {"list": []},
    "time": {"from": "now-3h", "to": "now"},
    "timepicker": {},
    "timezone": "browser",
    "title": "2JCIE-BU",
    "uid": "2jcie-bu",
    "version": 1,
    "weekStart": "",
}

with open(OUT, "w") as f:
    json.dump(dashboard, f, ensure_ascii=False, indent=2)
print(f"panels={len(panels)} -> {OUT}")
