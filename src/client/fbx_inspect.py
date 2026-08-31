"""Independent binary-FBX reader — the non-Blender verification path
(owner amendment 1, T2 review).

Blender exports Z-up and imports Z-up: a Blender -> FBX -> Blender round
trip is self-consistent even when the file is wrong for a third-party
consumer. This module is the third party. It parses the file's own
declarations (GlobalSettings axis convention, UnitScaleFactor, FBX version,
creator) and its raw geometry (vertex arrays, polygon sizes) straight from
the binary records, using stdlib + numpy only — never Blender.

It also provides the chirality machinery the export verification needs:

- ``box_corner_cloud`` — analytic expected vertex positions for box-built
  test fixtures (the fixture itself lives in input/fixtures/).
- ``find_axis_mapping`` — discovers every signed axis permutation (+ uniform
  positive scale + translation) that maps one point cloud onto another and
  reports each mapping's determinant. A MIRRORED asset only matches
  det == -1 mappings; asserting the single match has det == +1 proves no
  handedness flip without assuming any convention up front.
- ``build_minimal_fbx`` — fabricates a minimal well-formed binary FBX. Used
  by pure tests and stub exporters; the writer/ parser round trip is itself
  a parser test.

Binary FBX format notes (7.x): 23-byte magic, uint32 version; node records
are (end_offset, num_properties, property_list_len, name_len, name,
properties, nested nodes) with 32-bit fields for version < 7500 and 64-bit
for >= 7500; nested lists terminate with an all-zero null record; arrays
may be zlib-compressed (encoding 1).
"""

from __future__ import annotations

import itertools
import math
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FBX_MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"

_AXIS_NAMES = ("x", "y", "z")
_ARRAY_DTYPES = {"f": "<f4", "d": "<f8", "l": "<i8", "i": "<i4", "b": "|u1"}


# ── Parsed model ─────────────────────────────────────────────────────────────


@dataclass
class FbxAxes:
    """The axis convention the file DECLARES in GlobalSettings. Axis values
    index x/y/z; signs are +1/-1. What a conforming third-party loader uses
    to orient the model."""

    up_axis: int
    up_axis_sign: int
    front_axis: int
    front_axis_sign: int
    coord_axis: int
    coord_axis_sign: int
    unit_scale_factor: float | None = None

    def to_dict(self) -> dict:
        return {
            "up_axis": _AXIS_NAMES[self.up_axis], "up_axis_sign": self.up_axis_sign,
            "front_axis": _AXIS_NAMES[self.front_axis], "front_axis_sign": self.front_axis_sign,
            "coord_axis": _AXIS_NAMES[self.coord_axis], "coord_axis_sign": self.coord_axis_sign,
            "unit_scale_factor": self.unit_scale_factor,
        }


@dataclass
class FbxModel:
    """An FBX Model (transform node) from Objects. Only the Lcl* properties
    Blender's exporter writes are captured; absent entries mean identity
    (translation 0, rotation 0, scale 1) per the FBX default."""

    uid: int
    name: str
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scaling: tuple[float, float, float] = (1.0, 1.0, 1.0)


@dataclass
class FbxGeometry:
    vertices: np.ndarray  # (N, 3) float64, object-local, file units
    polygon_sizes: list[int]  # vertex count per polygon
    uid: int | None = None


def _euler_xyz_matrix(rx: float, ry: float, rz: float) -> np.ndarray:
    """FBX default RotationOrder (eEulerXYZ) as a column-vector rotation
    matrix: R = Rz @ Ry @ Rx (degrees in). Blender's exporter bakes the
    axis conversion into mesh data, so its Model rotations are identity —
    if that ever changes, the chiral export test fails and this is why."""
    ax, ay, az = math.radians(rx), math.radians(ry), math.radians(rz)
    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    cz, sz = math.cos(az), math.sin(az)
    rx_m = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry_m = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz_m = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rz_m @ ry_m @ rx_m


@dataclass
class FbxInfo:
    version: int  # e.g. 7400 => "FBX 7.4" (Blender writes 7.4 binary)
    creator: str
    axes: FbxAxes
    geometries: list[FbxGeometry] = field(default_factory=list)
    models: dict[int, FbxModel] = field(default_factory=dict)
    # ("OO"|"OP", child_uid, parent_uid) — object links from Connections
    connections: list[tuple[str, int, int]] = field(default_factory=list)

    def all_vertices(self) -> np.ndarray:
        """Raw object-local vertices, concatenated. Topology only — use
        world_vertices() for anything spatial."""
        if not self.geometries:
            return np.zeros((0, 3))
        return np.vstack([g.vertices for g in self.geometries])

    def world_vertices(self, in_metres: bool = True) -> np.ndarray:
        """All geometry vertices in the file's declared world space: each
        Geometry transformed by its connected Model chain (Lcl Translation /
        Rotation / Scaling, InheritType RrSs), then optionally normalised to
        metres via UnitScaleFactor (FBX's native unit is the centimetre:
        metres = value * UnitScaleFactor / 100).

        Blender's exporter bakes the axis conversion into the mesh data and
        writes Model translations in the file's axis system, so the result
        is in the convention GlobalSettings DECLARES — i.e. what a
        conforming third-party loader (their Babylon viewer) shows. This is
        deliberately not a Blender round trip."""
        if not self.geometries:
            return np.zeros((0, 3))
        geom_owner: dict[int, int] = {}
        model_parent: dict[int, int] = {}
        model_uids, geom_uids = set(self.models), {g.uid for g in self.geometries if g.uid is not None}
        for ctype, child, parent in self.connections:
            if ctype != "OO":
                continue
            if child in model_uids and parent in model_uids:
                model_parent[child] = parent
            elif child in geom_uids and parent in model_uids:
                geom_owner[child] = parent

        def model_world(uid: int | None, seen: frozenset[int]) -> np.ndarray:
            if uid is None or uid not in self.models or uid in seen:
                return np.eye(4)
            m = self.models[uid]
            local = np.eye(4)
            local[:3, :3] = _euler_xyz_matrix(*m.rotation_deg) @ np.diag(np.asarray(m.scaling))
            local[:3, 3] = m.translation
            return model_world(model_parent.get(uid), seen | {uid}) @ local

        factor = 1.0
        if in_metres:
            factor = float(self.axes.unit_scale_factor or 1.0) / 100.0
        out = []
        for g in self.geometries:
            world = model_world(geom_owner.get(g.uid), frozenset())
            pts = (world[:3, :3] @ g.vertices.T).T + world[:3, 3]
            out.append(pts * factor)
        return np.vstack(out)

    def world_extents_m(self) -> list[float]:
        """Sorted (descending) world-space extents in metres — comparable
        across axis conventions (a per-axis compare would be convention-
        dependent; the chiral test owns the directional proof)."""
        v = self.world_vertices()
        if not len(v):
            return []
        return sorted((v.max(axis=0) - v.min(axis=0)).tolist(), reverse=True)

    def ngon_count(self) -> int:
        return sum(1 for g in self.geometries for s in g.polygon_sizes if s > 4)

    def faces_total(self) -> int:
        return sum(len(g.polygon_sizes) for g in self.geometries)

    def triangle_equivalent(self) -> int:
        return sum(max(s - 2, 0) for g in self.geometries for s in g.polygon_sizes)


# ── Binary record parser ─────────────────────────────────────────────────────


def _null_record_len(version: int) -> int:
    return 25 if version >= 7500 else 13


def _parse_property(buf: bytes, pos: int):
    t = chr(buf[pos])
    pos += 1
    if t == "Y":
        return struct.unpack_from("<h", buf, pos)[0], pos + 2
    if t == "C":
        return bool(buf[pos]), pos + 1
    if t == "I":
        return struct.unpack_from("<i", buf, pos)[0], pos + 4
    if t == "F":
        return struct.unpack_from("<f", buf, pos)[0], pos + 4
    if t == "D":
        return struct.unpack_from("<d", buf, pos)[0], pos + 8
    if t == "L":
        return struct.unpack_from("<q", buf, pos)[0], pos + 8
    if t in _ARRAY_DTYPES:
        array_len, encoding, comp_len = struct.unpack_from("<III", buf, pos)
        pos += 12
        raw = bytes(buf[pos:pos + comp_len])
        pos += comp_len
        if encoding == 1:
            raw = zlib.decompress(raw)
        return np.frombuffer(raw, dtype=_ARRAY_DTYPES[t], count=array_len), pos
    if t in ("S", "R"):
        length = struct.unpack_from("<I", buf, pos)[0]
        pos += 4
        data = bytes(buf[pos:pos + length])
        pos += length
        if t == "S":
            return data.decode("utf-8", "replace"), pos
        return data, pos
    raise ValueError(f"Unknown FBX property type {t!r} at offset {pos - 1}")


def _parse_node(buf: bytes, pos: int, version: int):
    """Parse one node record. Returns (node_tuple | None, new_pos); None =
    null record (terminates a nested list)."""
    if version >= 7500:
        end_offset, num_props, prop_list_len = struct.unpack_from("<QQQ", buf, pos)
        sizes_len, offset_fmt = 24, "<QQQ"
    else:
        end_offset, num_props, prop_list_len = struct.unpack_from("<III", buf, pos)
        sizes_len, offset_fmt = 12, "<III"
    name_len = buf[pos + sizes_len]
    rec_header_len = sizes_len + 1 + name_len
    if end_offset == 0 and num_props == 0 and prop_list_len == 0 and name_len == 0:
        return None, pos + rec_header_len
    name = buf[pos + sizes_len + 1: pos + rec_header_len].decode("ascii", "replace")
    p = pos + rec_header_len
    props = []
    props_end = p + prop_list_len
    while p < props_end:
        value, p = _parse_property(buf, p)
        props.append(value)
    children = []
    if p < end_offset:
        children, p = _parse_nodes_until(buf, p, end_offset, version)
    return (name, props, children), p


def _parse_nodes_until(buf: bytes, pos: int, end: int, version: int):
    nodes = []
    null_len = _null_record_len(version)
    zeros = b"\x00" * null_len
    while pos < end:
        if end - pos < null_len:
            break  # trailing padding, not a node
        if buf[pos:pos + null_len] == zeros:
            pos += null_len  # null record terminates this list
            break
        node, pos = _parse_node(buf, pos, version)
        nodes.append(node)
    return nodes, pos


def _find_nodes(nodes, name, recursive=True):
    found = [n for n in nodes if n[0] == name]
    if recursive:
        for n in nodes:
            found.extend(_find_nodes(n[2], name, recursive=True))
    return found


def read_fbx_info(path: str | Path) -> FbxInfo:
    """Parse a binary FBX file (Blender's exporter output). Raises loudly on
    anything it cannot interpret — an auditable deliverable must parse."""
    buf = Path(path).read_bytes()
    if not buf.startswith(FBX_MAGIC):
        raise ValueError(f"{path}: not a binary FBX file (expected binary FBX 7.x)")
    version = struct.unpack_from("<I", buf, len(FBX_MAGIC))[0]
    if version < 7000:
        raise ValueError(f"{path}: FBX version {version} is not a 7.x binary file")
    top, _ = _parse_nodes_until(buf, len(FBX_MAGIC) + 4, len(buf), version)

    creators = _find_nodes([n for n in top if n[0] == "FBXHeaderExtension"], "Creator")
    creator = str(creators[0][1][0]) if creators else ""

    gs_nodes = [n for n in top if n[0] == "GlobalSettings"]
    if not gs_nodes:
        raise ValueError(f"{path}: no GlobalSettings node — cannot read axis convention")
    p70 = [c for c in gs_nodes[0][2] if c[0] == "Properties70"]
    if not p70:
        raise ValueError(f"{path}: GlobalSettings has no Properties70")
    values: dict[str, float] = {}
    for p_node in p70[0][2]:
        if p_node[0] != "P" or not p_node[1]:
            continue
        key = str(p_node[1][0])
        values[key] = p_node[1][-1]  # last property is the value
    try:
        axes = FbxAxes(
            up_axis=int(values["UpAxis"]), up_axis_sign=int(values["UpAxisSign"]),
            front_axis=int(values["FrontAxis"]), front_axis_sign=int(values["FrontAxisSign"]),
            coord_axis=int(values["CoordAxis"]), coord_axis_sign=int(values["CoordAxisSign"]),
            unit_scale_factor=float(values["UnitScaleFactor"]) if "UnitScaleFactor" in values else None,
        )
    except KeyError as e:
        raise ValueError(f"{path}: GlobalSettings missing axis field {e}") from e

    geometries = []
    for geom in _find_nodes(top, "Geometry"):
        verts = [c for c in geom[2] if c[0] == "Vertices"]
        pvi = [c for c in geom[2] if c[0] == "PolygonVertexIndex"]
        if not verts:
            continue
        vertices = np.asarray(verts[0][1][0], dtype=np.float64).reshape(-1, 3)
        polygon_sizes: list[int] = []
        if pvi:
            run = 0
            for idx in np.asarray(pvi[0][1][0]):
                run += 1
                if int(idx) < 0:  # last index of a polygon is stored bit-inverted
                    polygon_sizes.append(run)
                    run = 0
            if run:
                polygon_sizes.append(run)
        uid = geom[1][0] if geom[1] and isinstance(geom[1][0], int) else None
        geometries.append(FbxGeometry(vertices=vertices, polygon_sizes=polygon_sizes, uid=uid))

    models: dict[int, FbxModel] = {}
    for model_node in _find_nodes(top, "Model"):
        props = model_node[1]
        if not props or not isinstance(props[0], int):
            continue
        raw_name = str(props[1]) if len(props) > 1 else ""
        translation = (0.0, 0.0, 0.0)
        rotation_deg = (0.0, 0.0, 0.0)
        scaling = (1.0, 1.0, 1.0)
        for p70 in (c for c in model_node[2] if c[0] == "Properties70"):
            for p in p70[2]:
                if p[0] != "P" or not p[1]:
                    continue
                key = str(p[1][0])
                vals = [float(v) for v in p[1][4:] if isinstance(v, (int, float))]
                if len(vals) < 3:
                    continue
                if key == "Lcl Translation":
                    translation = tuple(vals[:3])
                elif key == "Lcl Rotation":
                    rotation_deg = tuple(vals[:3])
                elif key == "Lcl Scaling":
                    scaling = tuple(vals[:3])
        models[int(props[0])] = FbxModel(
            uid=int(props[0]), name=raw_name.split("\x00\x01")[0],
            translation=translation, rotation_deg=rotation_deg, scaling=scaling)

    connections: list[tuple[str, int, int]] = []
    for conn in _find_nodes(top, "Connections"):
        for c in conn[2]:
            if c[0] == "C" and len(c[1]) >= 3:
                connections.append((str(c[1][0]), int(c[1][1]), int(c[1][2])))

    return FbxInfo(version=version, creator=creator, axes=axes, geometries=geometries,
                   models=models, connections=connections)


# ── Minimal binary-FBX writer (tests + stub exporters) ──────────────────────


def _p_str(s: str) -> bytes:
    return b"S" + struct.pack("<I", len(s)) + s.encode("utf-8")


def _p_int(i: int) -> bytes:
    return b"I" + struct.pack("<i", int(i))


def _p_double(d: float) -> bytes:
    return b"D" + struct.pack("<d", float(d))


def _p_double_array(arr) -> bytes:
    data = np.asarray(arr, dtype="<f8").tobytes()
    return b"d" + struct.pack("<III", len(np.asarray(arr).ravel()), 0, len(data)) + data


def _p_int_array(arr) -> bytes:
    data = np.asarray(arr, dtype="<i4").tobytes()
    return b"i" + struct.pack("<III", len(np.asarray(arr).ravel()), 0, len(data)) + data


def _ser_node(name: str, props: list[bytes], children: list[bytes], base: int, version: int) -> bytes:
    name_b = name.encode("ascii")
    if version >= 7500:
        sizes_len, fmt = 24, "<QQQ"
    else:
        sizes_len, fmt = 12, "<III"
    props_blob = b"".join(props)
    children_blob = b"".join(children) + (b"\x00" * _null_record_len(version) if children else b"")
    end = base + sizes_len + 1 + len(name_b) + len(props_blob) + len(children_blob)
    header = struct.pack(fmt, end, len(props), len(props_blob)) + bytes([len(name_b)]) + name_b
    return header + props_blob + children_blob


def _ser_tree(name: str, props: list[bytes], children: list[tuple], base: int, version: int) -> bytes:
    """children: list of (name, props, children) tuples."""
    offset = base + (24 if version >= 7500 else 12) + 1 + len(name.encode()) + len(b"".join(props))
    child_blobs = []
    for child_name, child_props, child_children in children:
        blob = _ser_tree(child_name, child_props, child_children, offset, version)
        child_blobs.append(blob)
        offset += len(blob)
    return _ser_node(name, props, child_blobs, base, version)


def build_minimal_fbx(axes: FbxAxes | None = None, vertices=None,
                      polygon_vertex_index=None, creator: str = "minimal-fbx-writer",
                      version: int = 7400, models: list[dict] | None = None,
                      geometry_uid: int | None = None) -> bytes:
    """A minimal well-formed binary FBX carrying GlobalSettings (and optional
    geometry + Model transforms + Connections). For tests and stub exporters
    — NOT a substitute for real Blender output.

    ``models``: [{"uid": int, "name": str, "translation": (x, y, z),
    "parent_uid": int (0 = scene root), "geometry_uid": int}, ...]. Only one
    geometry is supported; ``geometry_uid`` links it to the owning model."""
    axes = axes or FbxAxes(1, 1, 2, -1, 0, 1, 1.0)  # FBX-standard Y-up
    p_entries = [
        ("UpAxis", _p_int(axes.up_axis), "int", "Integer"),
        ("UpAxisSign", _p_int(axes.up_axis_sign), "int", "Integer"),
        ("FrontAxis", _p_int(axes.front_axis), "int", "Integer"),
        ("FrontAxisSign", _p_int(axes.front_axis_sign), "int", "Integer"),
        ("CoordAxis", _p_int(axes.coord_axis), "int", "Integer"),
        ("CoordAxisSign", _p_int(axes.coord_axis_sign), "int", "Integer"),
    ]
    if axes.unit_scale_factor is not None:
        p_entries.append(("UnitScaleFactor", _p_double(axes.unit_scale_factor), "double", "Number"))
    p70_children = []
    for name, prop, type_label, type_label_long in p_entries:
        p70_children.append(
            ("P", [_p_str(name), _p_str(type_label), _p_str(type_label_long), _p_str(""), prop], [])
        )

    objects_children = []
    if vertices is not None:
        geometry_children = [("Vertices", [_p_double_array(vertices)], [])]
        if polygon_vertex_index is not None:
            geometry_children.append(("PolygonVertexIndex", [_p_int_array(polygon_vertex_index)], []))
        if geometry_uid is not None:
            objects_children.append(("Geometry",
                                     [_p_int(geometry_uid), _p_str("geo\x00\x01Geometry"), _p_str("Mesh")],
                                     geometry_children))
        else:
            objects_children.append(("Geometry", [_p_str("mesh")], geometry_children))
    if not objects_children:
        objects_children.append(("Node", [_p_str("empty")], []))

    for m in models or []:
        model_p_nodes = []
        p_props = [_p_str("Lcl Translation"), _p_str("Lcl Translation"), _p_str(""), _p_str("A")]
        p_props += [_p_double(float(v)) for v in m.get("translation", (0.0, 0.0, 0.0))]
        model_p_nodes.append(("P", p_props, []))
        if "rotation" in m:
            r_props = [_p_str("Lcl Rotation"), _p_str("Lcl Rotation"), _p_str(""), _p_str("A")]
            r_props += [_p_double(float(v)) for v in m["rotation"]]
            model_p_nodes.append(("P", r_props, []))
        objects_children.append(("Model",
                                 [_p_int(m["uid"]), _p_str(m["name"] + "\x00\x01Model"), _p_str("Mesh")],
                                 [("Properties70", [], model_p_nodes)]))

    top: list[tuple] = [
        ("FBXHeaderExtension", [], [("Creator", [_p_str(creator)], [])]),
        ("GlobalSettings", [], [("Properties70", [], p70_children)]),
        ("Objects", [], objects_children),
    ]
    if models:
        conn_children = [("C", [_p_str("OO"), _p_int(m["uid"]), _p_int(m.get("parent_uid", 0))], [])
                         for m in models]
        if geometry_uid is not None:
            owner = next((m["uid"] for m in models if m.get("geometry_uid") == geometry_uid),
                         models[0]["uid"])
            conn_children.insert(0, ("C", [_p_str("OO"), _p_int(geometry_uid), _p_int(owner)], []))
        top.append(("Connections", [], conn_children))
    out = bytearray()
    out += FBX_MAGIC
    out += struct.pack("<I", version)
    offset = len(out)
    for name, props, children in top:
        blob = _ser_tree(name, props, children, offset, version)
        out += blob
        offset += len(blob)
    out += b"\x00" * _null_record_len(version)
    return bytes(out)


# ── Chirality machinery ──────────────────────────────────────────────────────


def box_corner_cloud(boxes: list[tuple]) -> np.ndarray:
    """Analytic corner positions of centre-anchored boxes.
    ``boxes``: [(dimensions [dx, dy, dz], position [cx, cy, cz]), ...] →
    (8N, 3) array. The expected point cloud for box-built fixtures."""
    points = []
    for dims, pos in boxes:
        half = np.asarray(dims, dtype=np.float64) / 2.0
        centre = np.asarray(pos, dtype=np.float64)
        for sx in (-1, 1):
            for sy in (-1, 1):
                for sz in (-1, 1):
                    points.append(centre + np.array([sx * half[0], sy * half[1], sz * half[2]]))
    return np.asarray(points)


def _hausdorff(a: np.ndarray, b: np.ndarray) -> float:
    """Max nearest-neighbour distance both ways (small clouds; brute force)."""
    if len(a) == 0 or len(b) == 0:
        return float("inf")
    d = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    return float(max(d.min(axis=1).max(), d.min(axis=0).max()))


def _describe_mapping(matrix: np.ndarray) -> str:
    """Human-readable form of dst = matrix @ src, e.g.
    'blender(x,y,z) = file(x, z, -y)'."""
    parts = []
    for i, axis in enumerate(_AXIS_NAMES):
        j = int(np.argmax(np.abs(matrix[i])))
        sign = "+" if matrix[i, j] > 0 else "-"
        parts.append(f"{'' if sign == '+' else '-'}{_AXIS_NAMES[j]}")
    return f"blender(x,y,z) = file({', '.join(parts)})"


def find_axis_mapping(src: np.ndarray, dst: np.ndarray, tol: float = 1e-3) -> list[dict]:
    """Every signed axis permutation mapping point-set ``src`` onto ``dst``
    (uniform positive scale + translation allowed; point order irrelevant).
    Each result: {matrix, det, scale, residual, description}. With a chiral,
    axis-distinct fixture exactly ONE mapping matches — and its determinant
    is +1 only if no handedness flip occurred."""
    src_c = np.asarray(src, dtype=np.float64)
    dst_c = np.asarray(dst, dtype=np.float64)
    src_c = src_c - src_c.mean(axis=0)
    dst_c = dst_c - dst_c.mean(axis=0)
    src_ext = src_c.max(axis=0) - src_c.min(axis=0)
    dst_ext = dst_c.max(axis=0) - dst_c.min(axis=0)
    if np.any(src_ext < 1e-12) or np.any(dst_ext < 1e-12):
        raise ValueError("point clouds must be 3-dimensional (non-degenerate extents)")

    results = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            matrix = np.zeros((3, 3))
            for i in range(3):
                matrix[i, perm[i]] = signs[i]
            mapped = src_c @ matrix.T
            ext = mapped.max(axis=0) - mapped.min(axis=0)
            if np.any(ext < 1e-12):
                continue
            scales = dst_ext / ext
            scale = float(scales.mean())
            if scale <= 0 or np.any(np.abs(scales - scale) > 0.01 * scale):
                continue  # not a uniform scale — wrong permutation
            residual = _hausdorff(mapped * scale, dst_c)
            if residual <= tol * max(scale, 1.0):
                results.append({
                    "matrix": matrix,
                    "det": int(round(float(np.linalg.det(matrix)))),
                    "scale": scale,
                    "residual": float(residual),
                    "description": _describe_mapping(matrix),
                })
    return results
