"""Pictures of a built layout, with the original project's own drawing code:
the 2D board exactly as the desktop app exports it, and the 3D view's studio
look rendered offscreen."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import qt  # noqa: F401
from optolayout.app import export_scene
from optolayout.model import Layout
from optolayout.scene import LayoutScene


def make_scene(layout: Layout) -> LayoutScene:
    """A layout scene, synced and auto-aimed as the app would show the file."""
    sc = LayoutScene()
    sc.set_layout(layout)
    sc.sync_from_schematic()
    sc.orient_all()
    sc.check_clashes()
    return sc


def render_2d(scene: LayoutScene, png: Path, svg: Path | None = None, px_per_mm: float = 3.0) -> None:
    export_scene(scene, str(png), px_per_mm=px_per_mm)
    if svg is not None:
        export_scene(scene, str(svg))


def render_3d(layout: Layout, png: Path, beam_height: float = 50.8, size=(1600, 1000)) -> None:
    import pyvista as pv
    from optolayout.view3d import frame_board, populate, setup_studio

    pl = pv.Plotter(off_screen=True, window_size=list(size))
    try:
        setup_studio(pl, "light")
        populate(pl, layout, beam_height, studio=True)
        frame_board(pl, layout, beam_height)
        t = layout.table
        # frame_board fits the whole bounding box with room to spare; a still picture can come closer,
        # more so for long narrow boards that leave the frame half empty
        pl.camera.zoom(1.55 if t.width / t.height >= 2 else 1.3)
        pl.screenshot(str(png))
    finally:
        pl.close()


def export_gltf(layout: Layout, path: Path, beam_height: float = 50.8, labels: dict[str, str] | None = None) -> None:
    """The same 3D scene as a glTF file for the browser's interactive view.

    VTK's exporter keeps meshes and PBR materials but drops two things the
    picture relies on, so they are put back from the actors (the glTF meshes
    come out in the renderer's actor order): translucency (glass, beam halos)
    and unlit beams. Each node is named after its actor and carries the part's
    label, so the viewer can say what is under the mouse. The contact shadows,
    drawn for the still picture, are left out."""
    import pyvista as pv
    from optolayout.view3d import populate, setup_studio

    pl = pv.Plotter(off_screen=True)
    try:
        setup_studio(pl, "light")
        populate(pl, layout, beam_height, studio=True)
        actors = list(pl.renderer.actors.items())
        pl.export_gltf(str(path), inline_data=True, rotate_scene=True)
    finally:
        pl.close()
    g = json.loads(path.read_text(encoding="utf-8"))
    if len(g.get("meshes", [])) != len(actors):
        return  # unexpected exporter output: keep it as exported
    labels = labels or {}
    unlit = False
    drop = set()
    for i, (name, actor) in enumerate(actors):
        mat = g["materials"][g["meshes"][i]["primitives"][0].get("material", i)]
        if actor.prop.opacity < 1:
            mat["alphaMode"] = "BLEND"
            mat["doubleSided"] = True
        if not actor.prop.lighting:
            mat.setdefault("extensions", {})["KHR_materials_unlit"] = {}
            unlit = True
        if name == "contact_shadows":
            drop.add(i)
    for node in g["nodes"]:
        m = node.get("mesh")
        if m is None:
            continue
        name = actors[m][0]
        node["name"] = name
        part = re.match(r"(c_p\d+)_\d+$", name)
        if part:
            node["extras"] = {"part": part.group(1), "label": labels.get(part.group(1), "")}
        elif name.startswith("beam_"):
            node["extras"] = {"beam": True}
    if drop:
        for sc in g.get("scenes", []):
            sc["nodes"] = [n for n in sc["nodes"] if g["nodes"][n].get("mesh") not in drop]
        for node in g["nodes"]:
            if "children" in node:
                node["children"] = [n for n in node["children"] if g["nodes"][n].get("mesh") not in drop]
    if unlit:
        g["extensionsUsed"] = sorted(set(g.get("extensionsUsed", [])) | {"KHR_materials_unlit"})
    path.write_text(json.dumps(g, separators=(",", ":")), encoding="utf-8")
