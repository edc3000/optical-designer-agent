"""One build: description -> layout -> checks -> pictures. Used by the worker and the CLI."""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import checks
from .render import export_gltf, make_scene, render_2d, render_3d
from .spec import PARTS, Built, build


def summary(built: Built, scene) -> str:
    lay = scene.layout
    t = lay.table
    w, h = built.board_in
    lines = [f"面包板 {w}×{h} 英寸（1 英寸孔距），光束高度 {built.beam_height:g} mm，"
             f"{len(lay.components)} 个元件，{len(lay.beams)} 段光束。", "元件："]
    by_label = {c.label: c for c in lay.components.values()}
    for lab, p in built.placed.items():
        c = by_label[lab]
        hole = t.hole_at(c.x, c.y)
        where = f"{hole[0]}" + ("" if abs(hole[1]) < 0.5 and abs(hole[2]) < 0.5 else " 附近") if hole else "板外"
        extra = f"，f = {p.f:g} mm" if p.f else ""
        lines.append(f"- {lab}：{PARTS[p.part][1]}，孔位 {where}，朝向 {c.angle:.0f}°{extra}")
    lines.append("光束（相邻元件间距离）：")
    for b in lay.beams.values():
        s, e = lay.components[b.source], lay.components[b.target]
        lines.append(f"- {s.label}→{e.label}：{lay.beam_length(b):.1f} mm")
    return "\n".join(lines)


def run(spec: dict, out_dir: Path, render3d: bool = True) -> dict:
    """Returns {ok, errors, warnings, summary, files, timings}. ok means no errors;
    pictures are made whenever a layout could be built, so a failed attempt can
    still be looked at."""
    out_dir.mkdir(parents=True, exist_ok=True)
    timings = {}
    t0 = time.time()
    built = build(spec)
    res = {"ok": False, "errors": list(built.errors), "warnings": [], "summary": "", "files": {}, "timings": timings}
    if built.layout is None:
        if not res["errors"]:
            res["errors"].append("没有可放置的元件")
        return res
    scene = make_scene(built.layout)
    rep = checks.run(built, scene)
    res["errors"] += rep.errors
    res["warnings"] = rep.warnings
    res["summary"] = summary(built, scene)
    res["board_in"] = list(built.board_in)
    timings["build_check"] = round(time.time() - t0, 2)
    files = res["files"]
    layout_json = out_dir / "layout.json"
    scene.layout.save(layout_json)
    (out_dir / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    files["layout"] = str(layout_json)
    t1 = time.time()
    render_2d(scene, out_dir / "layout_2d.png", out_dir / "layout_2d.svg")
    files["png2d"], files["svg2d"] = str(out_dir / "layout_2d.png"), str(out_dir / "layout_2d.svg")
    timings["render2d"] = round(time.time() - t1, 2)
    if render3d:
        t2 = time.time()
        render_3d(scene.layout, out_dir / "layout_3d.png", built.beam_height)
        files["png3d"] = str(out_dir / "layout_3d.png")
        labels = {cid: f"{lab}（{PARTS[built.placed[lab].part][1]}"
                  + (f"，f = {built.placed[lab].f:g} mm" if built.placed[lab].f else "") + "）"
                  for lab, cid in built.comp_id.items()}
        export_gltf(scene.layout, out_dir / "layout_3d.gltf", built.beam_height, labels)
        files["gltf3d"] = str(out_dir / "layout_3d.gltf")
        timings["render3d"] = round(time.time() - t2, 2)
    res["ok"] = not res["errors"]
    res["board_mm"] = [scene.layout.table.width, scene.layout.table.height]
    return res
