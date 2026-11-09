"""Beam-path description (what the agent writes) -> an optolayout Layout.

The model never writes coordinates. It walks the beam like a turtle: start at a
part, go so many mm, place the next part, turn at mirrors. Branches start again
from a beam splitter. This module turns that walk into positions, sizes a
standard breadboard around it and builds the Layout the original project reads.

Directions are degrees, counter-clockwise as seen on screen, 0 = +x (right);
layout coordinates are mm with y pointing down (optolayout's convention).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import qt  # noqa: F401  (a QApplication must exist before symbol bounds are read)
from optolayout.model import Component, Layout, SchElement, SchLink, Table, WAVELENGTH_COLORS, kind_of_type
from optolayout.orient import auto_angle
from optolayout.symbols import SYMBOLS

IN = 25.4
MARGIN = 25.4  # free board around the outermost part outline (mm)

# semantic part -> (layout type on a 1" grid breadboard, Chinese name)
PARTS: dict[str, tuple[str, str]] = {
    "laser": ("laser", "激光器"),
    "mirror": ("mirror_1in", "反射镜"),
    "lens": ("lens_1in", "透镜"),
    "hwp": ("hwp", "半波片"),
    "qwp": ("qwp", "四分之一波片"),
    "pbs": ("pbs", "偏振分束棱镜"),
    "bs": ("bs", "非偏振分束棱镜"),
    "aom": ("aom", "声光调制器"),
    "iris": ("iris", "光阑"),
    "fiber": ("fiber", "光纤耦合器"),
    "shutter": ("shutter", "快门"),
    "dump": ("dump", "光束收集器"),
    "photodiode": ("photodiode", "探测器 / 相机"),
}
SPLITTERS = {"pbs", "bs"}
TURNS = {"left": 90.0, "right": -90.0, "straight": 0.0}

# breadboards sold in these sizes (inches, width x height); the smallest that fits is used
BOARD_SIZES = [(12, 12), (18, 12), (12, 18), (24, 12), (12, 24), (18, 18), (36, 12), (48, 12), (24, 18), (18, 24), (24, 24),
               (36, 24), (24, 36), (48, 24), (36, 36), (48, 36), (48, 48), (60, 36), (72, 36), (96, 48)]


@dataclass
class Placed:
    label: str
    part: str
    x: float
    y: float
    dir_in: float  # direction of travel arriving here (for the first part of a path: leaving)
    path: str
    f: float | None = None
    note: str = ""
    retro: bool = False


@dataclass
class Built:
    spec: dict
    layout: Layout | None = None
    placed: dict[str, Placed] = field(default_factory=dict)  # label -> part
    chains: list[list[str]] = field(default_factory=list)  # beams, as label chains
    comp_id: dict[str, str] = field(default_factory=dict)  # label -> component id
    errors: list[str] = field(default_factory=list)
    beam_height: float = 50.8
    board_in: tuple[int, int] | None = None


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def wavelength_color(nm: float | None) -> str:
    """The project's colour for a known wavelength, else an approximate spectrum colour."""
    if nm is None:
        return WAVELENGTH_COLORS["Other"][1]
    for wl, color in WAVELENGTH_COLORS.values():
        if wl is not None and abs(wl - nm) <= 10:
            return color
    if nm < 380 or nm > 780:
        return "#6b2b2b" if nm > 780 else "#7b3fb0"
    stops = [(380, (110, 0, 160)), (440, (40, 40, 255)), (490, (0, 170, 220)), (510, (0, 170, 60)),
             (580, (225, 190, 0)), (645, (240, 60, 20)), (780, (150, 0, 20))]
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if a <= nm <= b:
            t = (nm - a) / (b - a)
            r, g, bl = (round(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))
            return f"#{r:02x}{g:02x}{bl:02x}"
    return WAVELENGTH_COLORS["Other"][1]


def walk(spec: dict, out: Built) -> None:
    """Place every step of every path (positions relative to the first part)."""
    paths = spec.get("paths")
    if not isinstance(paths, list) or not paths:
        out.errors.append("spec.paths 必须是非空数组")
        return
    for pi, path in enumerate(paths):
        pid = str(path.get("id") or f"path{pi + 1}")
        steps = path.get("steps")
        if not isinstance(steps, list) or not steps:
            out.errors.append(f"路径 {pid}: steps 必须是非空数组")
            continue
        chain: list[str] = []
        if path.get("from") is not None:
            src = path["from"]
            if src not in out.placed:
                out.errors.append(f"路径 {pid}: from=\"{src}\" 不是前面已放置的元件标签")
                continue
            sp = out.placed[src]
            if sp.part not in SPLITTERS:
                out.errors.append(f"路径 {pid}: 只能从分束元件（pbs/bs）分出支路，\"{src}\" 是 {sp.part}")
                continue
            turn = path.get("turn", "left")
            if turn not in TURNS:
                out.errors.append(f"路径 {pid}: turn 只能是 left/right/straight，收到 {turn!r}")
                continue
            d = (sp.dir_in + TURNS[turn]) % 360
            cur: tuple[float, float] | None = (sp.x, sp.y)
            chain.append(src)
        else:
            if pi > 0:
                at = path.get("at") or {}
                x0, y0 = _num(at.get("x_mm")), _num(at.get("y_mm"))
                if x0 is None or y0 is None:
                    out.errors.append(f"路径 {pid}: 第二条及以后的独立路径需要 from（从分束器分出）"
                                      "或 at: {x_mm, y_mm}（相对第一条路径起点）")
                    continue
                cur = None
                start_xy = (x0, -y0)  # at.y_mm counts up (math convention), layout y points down
            else:
                cur, start_xy = None, (0.0, 0.0)
            d = _num(path.get("dir", 0))
            if d is None:
                out.errors.append(f"路径 {pid}: dir 必须是角度数字（0=向右，90=向上）")
                continue
        ended = False
        for si, st in enumerate(steps):
            where = f"路径 {pid} 第 {si + 1} 步"
            if ended:
                out.errors.append(f"{where}: 回射镜（turn=back）之后不能再有元件，返回光请用 from 分束器的新路径")
                break
            part, label = st.get("part"), str(st.get("label") or "").strip()
            if part not in PARTS:
                out.errors.append(f"{where}: 未知元件 {part!r}，可用：{', '.join(PARTS)}")
                break
            if not label:
                out.errors.append(f"{where}: 缺少 label")
                break
            if label in out.placed:
                out.errors.append(f"{where}: 标签 \"{label}\" 重复，每个元件的 label 必须唯一")
                break
            if cur is None:
                x, y = start_xy
            else:
                go = _num(st.get("go"))
                if go is None or go <= 0:
                    out.errors.append(f"{where}（{label}）: go 必须是正数（毫米，沿光束到上一个元件的距离）")
                    break
                x = cur[0] + go * math.cos(math.radians(d))
                y = cur[1] - go * math.sin(math.radians(d))
            f = _num(st.get("f"))
            if part == "lens" and (f is None or f <= 0):
                out.errors.append(f"{where}（{label}）: 透镜必须给出焦距 f（毫米，正数）")
            p = Placed(label, part, x, y, d, pid, f=f if part == "lens" else None, note=str(st.get("note") or ""))
            turn = st.get("turn")
            if turn is not None:
                if part != "mirror":
                    out.errors.append(f"{where}（{label}）: 只有 mirror 可以 turn，{part} 让光直行")
                elif turn == "back":
                    p.retro = ended = True
                elif turn in ("left", "right"):
                    d = (d + TURNS[turn]) % 360
                else:
                    out.errors.append(f"{where}（{label}）: turn 只能是 left/right/back")
            elif part == "mirror":
                out.errors.append(f"{where}（{label}）: mirror 必须给出 turn（left/right/back）")
            out.placed[label] = p
            chain.append(label)
            cur = (x, y)
        if len(chain) >= 2:
            out.chains.append(chain)


def _angles(out: Built) -> dict[str, float]:
    """Each part's angle as the app will aim it (optolayout.orient), from the beams around it."""
    ins: dict[str, list] = {k: [] for k in out.placed}
    outs: dict[str, list] = {k: [] for k in out.placed}
    for chain in out.chains:
        for a, b in zip(chain, chain[1:]):
            pa, pb = out.placed[a], out.placed[b]
            outs[a].append((pb.x, pb.y))
            ins[b].append((pa.x, pa.y))
    res = {}
    for k, p in out.placed.items():
        typ = PARTS[p.part][0]
        a = auto_angle(kind_of_type(typ), (p.x, p.y), ins[k], outs[k], typ)
        res[k] = p.dir_in if a is None else a
    return res


def _extent(p: Placed, angle: float) -> tuple[float, float, float, float]:
    """Outline box of a placed part, its drawing rotated as the app will draw it."""
    b = SYMBOLS[PARTS[p.part][0]].bounds
    a = math.radians(-angle)
    pts = [(cx * math.cos(a) - cy * math.sin(a), cx * math.sin(a) + cy * math.cos(a))
           for cx, cy in ((b.left(), b.top()), (b.right(), b.top()), (b.left(), b.bottom()), (b.right(), b.bottom()))]
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return p.x + min(xs), p.x + max(xs), p.y + min(ys), p.y + max(ys)


def choose_board(w_need: float, h_need: float, spec_board: dict) -> tuple[int, int] | None:
    wi, hi = _num(spec_board.get("width_in")), _num(spec_board.get("height_in"))
    if wi and hi:
        return (int(wi), int(hi)) if wi * IN >= w_need and hi * IN >= h_need else None
    fits = [s for s in BOARD_SIZES if s[0] * IN >= w_need and s[1] * IN >= h_need]
    return min(fits, key=lambda s: (s[0] * s[1], -s[0])) if fits else None


def make_layout(out: Built) -> None:
    spec = out.spec
    board = spec.get("board") or {}
    style = board.get("style", "grid")
    if style != "grid":
        out.errors.append(f"board.style 目前只支持 \"grid\"（1 英寸孔距面包板），收到 {style!r}")
        return
    bh = _num(board.get("beam_height_mm"))
    out.beam_height = bh if bh and 10 <= bh <= 300 else 50.8
    ang = _angles(out)
    ext = [_extent(p, ang[k]) for k, p in out.placed.items()]
    x0, x1 = min(e[0] for e in ext), max(e[1] for e in ext)
    y0, y1 = min(e[2] for e in ext), max(e[3] for e in ext)
    size = choose_board(x1 - x0 + 2 * MARGIN, y1 - y0 + 2 * MARGIN, board)
    if size is None:
        out.errors.append(f"光路占地 {(x1 - x0) / IN:.1f}×{(y1 - y0) / IN:.1f} 英寸，放不进指定或可选的面包板，"
                          "请缩短距离或去掉 board 尺寸限制")
        return
    out.board_in = size
    W, H = size[0] * IN, size[1] * IN
    t = Table(name=str(spec.get("title") or "Optical layout"), width=W, height=H)
    t.apply_style("grid")
    # centre the drawing, then shift so the first part sits on a hole
    tx, ty = (W - (x0 + x1)) / 2, (H - (y0 + y1)) / 2
    tx = t.hole_offset + round((tx - t.hole_offset) / t.hole_pitch) * t.hole_pitch
    ty = t.hole_offset + round((ty - t.hole_offset) / t.hole_pitch) * t.hole_pitch
    lay = Layout(table=t)
    nm = _num(spec.get("wavelength_nm"))
    color = wavelength_color(nm)
    for k, p in enumerate(out.placed.values(), start=1):
        p.x, p.y = round(p.x + tx, 3), round(p.y + ty, 3)
        typ = PARTS[p.part][0]
        eid = f"p{k}"
        notes = "; ".join(s for s in (f"f = {p.f:g} mm" if p.f else "", p.note) if s)
        lay.schematic.elements[eid] = SchElement(id=eid, kind=kind_of_type(typ), x=round(p.x * 1.6 / 10) * 10,
                                                 y=round(p.y * 1.6 / 10) * 10, label=p.label, part=typ,
                                                 notes=notes)
        cid = f"c_{eid}"
        out.comp_id[p.label] = cid
        lay.components[cid] = Component(id=cid, type=typ, x=p.x, y=p.y, angle=p.dir_in, label=p.label,
                                        sch_id=eid, notes=notes, pinned=True, auto_angle=True)
    for chain in out.chains:
        for a, b in zip(chain, chain[1:]):
            ea, eb = out.comp_id[a][2:], out.comp_id[b][2:]
            lid = f"k_{ea}_{eb}"
            lay.schematic.links[lid] = SchLink(id=lid, source=ea, target=eb, color=color, wavelength_nm=nm)
    out.layout = lay


def build(spec: dict) -> Built:
    out = Built(spec=spec)
    if not isinstance(spec, dict):
        out.errors.append("spec 必须是 JSON 对象")
        return out
    walk(spec, out)
    if out.placed and not out.errors:
        make_layout(out)
    return out
