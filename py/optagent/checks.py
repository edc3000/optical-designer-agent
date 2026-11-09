"""Checks on a built layout: geometry (overlaps, beams through parts) with the
project's own part outlines, and optics rules for the systems the demo targets.

Every problem is a short Chinese sentence naming the parts, so the agent can
fix the description and try again. "errors" must be fixed; "warnings" are
advice that a working design may still ignore.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPolygonF

from optolayout.scene import LayoutScene
from optolayout.symbols import SYMBOLS

from .spec import Built, Placed

TOL_MM = 1.0  # distances the agent asked for must come out this close
POST_CLEAR_MM = 25.0  # parts on posts: post holders and bases need about 1" between centres


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _seg_poly(a: QPointF, b: QPointF, half: float = 0.6) -> QPolygonF:
    """A thin rectangle along a beam segment, shortened at both ends so the
    parts the beam starts and ends on don't count as blocking it."""
    dx, dy = b.x() - a.x(), b.y() - a.y()
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    s = min(3.0, n / 4)
    a2, b2 = QPointF(a.x() + ux * s, a.y() + uy * s), QPointF(b.x() - ux * s, b.y() - uy * s)
    px, py = -uy * half, ux * half
    return QPolygonF([QPointF(a2.x() + px, a2.y() + py), QPointF(b2.x() + px, b2.y() + py),
                      QPointF(b2.x() - px, b2.y() - py), QPointF(a2.x() - px, a2.y() - py)])


def geometry(built: Built, scene: LayoutScene, rep: Report) -> None:
    lay = scene.layout
    label = {c.id: c.label for c in lay.components.values()}
    items = {cid: it for cid, it in scene.comp_items.items() if SYMBOLS[it.comp.type].physical}
    polys = {cid: it.outline() for cid, it in items.items()}
    ids = list(polys)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if polys[ids[i]].intersects(polys[ids[j]]):
                rep.errors.append(f"元件重叠：{label[ids[i]]} 与 {label[ids[j]]} 的底座/外形相交，请拉开距离")
    comps = [c for c in lay.components.values() if c.id in polys and c.type != "laser"]
    for i in range(len(comps)):
        for j in range(i + 1, len(comps)):
            a, b = comps[i], comps[j]
            d = math.hypot(a.x - b.x, a.y - b.y)
            if d < POST_CLEAR_MM:
                rep.errors.append(f"元件太近：{a.label} 与 {b.label} 中心相距 {d:.0f} mm，"
                                  f"支杆底座约需 {POST_CLEAR_MM:.0f} mm，请拉开")
    for b in lay.beams.values():
        s, t = lay.components[b.source], lay.components[b.target]
        seg = _seg_poly(QPointF(s.x, s.y), QPointF(t.x, t.y))
        for cid, poly in polys.items():
            if cid in (b.source, b.target):
                continue
            if poly.intersects(seg):
                rep.errors.append(f"光束 {s.label}→{t.label} 穿过了 {label[cid]}，请改变路径或移动元件")
    t = lay.table
    for c in lay.components.values():
        if not t.on_board(c.x, c.y):
            rep.errors.append(f"{c.label} 落在面包板外")


def _path_parts(built: Built, label: str) -> list[Placed]:
    """The parts of the chain that ends at `label`, in beam order, following the
    branch back into the chain it came from."""
    for chain in built.chains:
        if label in chain[1:]:
            idx = chain.index(label)
            head = chain[0]
            before = []
            if built.placed[head].path != built.placed[label].path:  # a branch: head is its splitter
                before = [p for p in _path_parts(built, head)]
                return before + [built.placed[x] for x in chain[1:idx + 1]]
            return [built.placed[x] for x in chain[:idx + 1]]
    return [built.placed[label]]


def _dist(a: Placed, b: Placed) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def optics(built: Built, rep: Report) -> None:
    placed = built.placed
    # AOM double pass: PBS -> AOM -> lens (f after the AOM) -> QWP -> retro mirror (f after the lens),
    # with the returning light leaving the PBS on a branch
    for chain in built.chains:
        for i, lab in enumerate(chain):
            p = placed[lab]
            if p.part != "aom":
                continue
            rest = [placed[x] for x in chain[i + 1:]]
            retro = next((q for q in rest if q.retro), None)
            if retro is None:
                continue  # single pass: nothing to check
            between = rest[:rest.index(retro)]
            lenses = [q for q in between if q.part == "lens"]
            if len(lenses) != 1:
                rep.errors.append(f"双通 {lab}：AOM 与回射镜 {retro.label} 之间应正好有一个猫眼透镜，现在有 {len(lenses)} 个")
            else:
                L = lenses[0]
                if L.f:
                    d1, d2 = _dist(p, L), _dist(L, retro)
                    if abs(d1 - L.f) > TOL_MM:
                        rep.errors.append(f"双通 {lab}：猫眼透镜 {L.label} 应在 AOM 后 f = {L.f:g} mm，现在是 {d1:.1f} mm")
                    if abs(d2 - L.f) > TOL_MM:
                        rep.errors.append(f"双通 {lab}：回射镜 {retro.label} 应在透镜 {L.label} 后 f = {L.f:g} mm，"
                                          f"现在是 {d2:.1f} mm")
            if not any(q.part == "qwp" for q in between):
                rep.errors.append(f"双通 {lab}：AOM 与回射镜之间缺少 QWP（往返两次转 90° 偏振，返回光才能从 PBS 另一端出射）")
            pbs = next((placed[x] for x in reversed(chain[:i]) if placed[x].part == "pbs"), None)
            if pbs is None:
                rep.errors.append(f"双通 {lab}：AOM 之前缺少 PBS（返回光要从 PBS 的另一端分出）")
            elif not any(c[0] == pbs.label and placed[c[1]].path != pbs.path for c in built.chains):
                rep.errors.append(f"双通 {lab}：缺少返回光的出射路径，请加一条 from=\"{pbs.label}\" 的支路")
    # fiber coupling: two steering mirrors of its own right before every coupler
    for lab, p in placed.items():
        if p.part != "fiber":
            continue
        parts = _path_parts(built, lab)[:-1]
        tail = []
        for q in reversed(parts):  # back to the last part that isn't a steering mirror or a waveplate
            if q.part in ("mirror", "hwp", "qwp", "iris", "shutter") and not q.retro:
                tail.append(q)
            else:
                break
        mirrors = [q for q in tail if q.part == "mirror"]
        if len(mirrors) < 2:
            rep.errors.append(f"光纤 {lab}：耦合前需要两面独立的导向镜（walk the beam 对准光纤用），现在只有 {len(mirrors)} 面")
        elif _dist(mirrors[1], mirrors[0]) < 50:
            rep.warnings.append(f"光纤 {lab}：两面导向镜 {mirrors[1].label}→{mirrors[0].label} 相距不到 50 mm，调节会很敏感")
    # lens pairs straight after each other: report their spacing (4f / telescope)
    for chain in built.chains:
        lenses = [placed[x] for x in chain if placed[x].part == "lens"]
        for a, b in zip(lenses, lenses[1:]):
            if a.f and b.f:
                d = _optical(built, a.label, b.label)
                if d is not None and abs(d - (a.f + b.f)) > TOL_MM:
                    rep.warnings.append(f"透镜 {a.label}(f={a.f:g}) 与 {b.label}(f={b.f:g}) 光程 {d:.1f} mm，"
                                        f"不等于 f1+f2 = {a.f + b.f:g} mm（若是 4f/望远镜系统需要相等）")


def _optical(built: Built, a: str, b: str) -> float | None:
    lay = built.layout
    r = lay.optical_path(built.comp_id[a], built.comp_id[b]) if lay else None
    return r[0] if r else None


def constraints(built: Built, rep: Report) -> None:
    for k, c in enumerate(built.spec.get("constraints") or [], start=1):
        if not isinstance(c, dict) or c.get("type") != "distance":
            rep.errors.append(f"约束 {k}：目前只支持 {{\"type\": \"distance\", \"a\", \"b\", \"mm\"}}")
            continue
        a, b, mm = c.get("a"), c.get("b"), c.get("mm")
        if a not in built.comp_id or b not in built.comp_id or not isinstance(mm, (int, float)):
            rep.errors.append(f"约束 {k}：a/b 必须是已放置元件的 label，mm 必须是数字")
            continue
        d = _optical(built, a, b)
        if d is None:
            rep.errors.append(f"约束 {k}：{a} 与 {b} 之间没有光路相连")
        elif abs(d - mm) > TOL_MM:
            rep.errors.append(f"约束 {k}：{a}→{b} 光程应为 {mm:g} mm，实际 {d:.1f} mm")


def run(built: Built, scene: LayoutScene) -> Report:
    rep = Report()
    geometry(built, scene, rep)
    optics(built, rep)
    constraints(built, rep)
    return rep
