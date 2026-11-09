"""The three demo systems build cleanly, and the checks catch the mistakes a model makes."""

import copy
import json
from pathlib import Path

import pytest

from optagent import pipeline

SPECS = Path(__file__).resolve().parents[2] / "examples" / "specs"


def load(name):
    return json.loads((SPECS / f"{name}.json").read_text(encoding="utf-8"))


def run(spec, tmp_path):
    return pipeline.run(spec, tmp_path, render3d=False)


@pytest.mark.parametrize("name", ["4f", "dp_aom", "fiber"])
def test_examples_pass(name, tmp_path):
    res = run(load(name), tmp_path)
    assert res["errors"] == [], res["errors"]
    assert res["ok"]
    for k in ("layout", "png2d", "svg2d"):
        assert Path(res["files"][k]).stat().st_size > 1000


def test_examples_render_3d(tmp_path):
    res = pipeline.run(load("dp_aom"), tmp_path, render3d=True)
    assert Path(res["files"]["png3d"]).stat().st_size > 10000
    g = json.loads(Path(res["files"]["gltf3d"]).read_text(encoding="utf-8"))
    names = {n.get("name") for n in g["nodes"]}
    assert "contact_shadows" not in {g["nodes"][c].get("name") for n in g["nodes"] for c in n.get("children", [])}
    assert "KHR_materials_unlit" in g["extensionsUsed"]  # beams glow, unshaded
    assert any(m.get("alphaMode") == "BLEND" for m in g["materials"])  # glass and beam halos
    labels = {n["extras"]["label"] for n in g["nodes"] if n.get("extras", {}).get("label")}
    assert "LD1（透镜，f = 150 mm）" in labels and "board" in names


def test_layout_opens_as_project_file(tmp_path):
    from optolayout.model import Layout
    res = run(load("dp_aom"), tmp_path)
    lay = Layout.load(res["files"]["layout"])
    assert len(lay.components) == 9 and len(lay.schematic.links) == 8
    assert all(c.sch_id in lay.schematic.elements for c in lay.components.values())


def test_wrong_4f_distance(tmp_path):
    spec = load("4f")
    spec["paths"][0]["steps"][4]["go"] = 120  # L2 too far from the Fourier plane
    res = run(spec, tmp_path)
    assert any("L1→L2" in e and "200" in e for e in res["errors"])
    assert any("f1+f2" in w for w in res["warnings"])


def test_double_pass_lens_distance(tmp_path):
    spec = load("dp_aom")
    spec["paths"][0]["steps"][4]["go"] = 120  # cat's eye lens not at f from the AOM
    res = run(spec, tmp_path)
    assert any("猫眼透镜 LD1" in e for e in res["errors"])


def test_double_pass_needs_qwp_and_return(tmp_path):
    spec = load("dp_aom")
    steps = spec["paths"][0]["steps"]
    steps[6]["go"] = 150  # mirror straight after the lens, QWP removed
    del steps[5]
    del spec["paths"][1]
    res = run(spec, tmp_path)
    assert any("QWP" in e for e in res["errors"])
    assert any("返回光的出射路径" in e for e in res["errors"])


def test_fiber_needs_two_mirrors(tmp_path):
    spec = load("fiber")
    steps = spec["paths"][0]["steps"]
    steps[2]["turn"] = "left"  # MB removed: MA turns once, straight to the fiber
    del steps[1]
    res = run(spec, tmp_path)
    assert any("两面独立的导向镜" in e for e in res["errors"])


def test_overlap_and_beam_through_part(tmp_path):
    spec = load("4f")
    spec["paths"][0]["steps"][2]["go"] = 8  # L1 jammed against the object plane iris
    res = run(spec, tmp_path)
    assert any("元件太近" in e for e in res["errors"])
    spec = load("dp_aom")
    # the return goes up, right, then straight down through AOM1 (80 mm right of PBS1)
    spec["paths"][1]["turn"] = "left"
    spec["paths"][1]["steps"] = [
        {"go": 60, "part": "mirror", "label": "MOUT", "turn": "right"},
        {"go": 80, "part": "mirror", "label": "MX", "turn": "right"},
        {"go": 120, "part": "dump", "label": "BD"},
    ]
    res = run(spec, tmp_path)
    assert any("MX→BD 穿过了 AOM1" in e for e in res["errors"]), res["errors"]


@pytest.mark.parametrize("mutate, needle", [
    (lambda s: s["paths"][0]["steps"][1].update(part="prism"), "未知元件"),
    (lambda s: s["paths"][0]["steps"][2].pop("f"), "焦距"),
    (lambda s: s["paths"][0]["steps"][2].update(label="物面"), "重复"),
    (lambda s: s["paths"][0]["steps"][2].update(go=-5), "go 必须是正数"),
    (lambda s: s["paths"][0]["steps"][1].update(turn="left"), "只有 mirror"),
])
def test_spec_errors(mutate, needle, tmp_path):
    spec = copy.deepcopy(load("4f"))
    mutate(spec)
    res = run(spec, tmp_path)
    assert not res["ok"]
    assert any(needle in e for e in res["errors"]), res["errors"]
