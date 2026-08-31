# 3D Builder — Headless Blender Automation Harness
#
# Invoked as:
#   blender --background --factory-startup --python harness_script.py -- <request.json>
#
# Request JSON shape: {"op": "<name>", "params": {...}}
# Results are printed between sentinel markers on stdout as JSON.
#
# This file runs inside Blender's bundled Python. It must stay self-contained:
# stdlib + bpy/bmesh/mathutils only — no project imports.

import json
import math
import os
import sys
import traceback

RESULT_BEGIN = "<<<3DBUILDER_RESULT_BEGIN>>>"
RESULT_END = "<<<3DBUILDER_RESULT_END>>>"

# Shapes whose mesh data starts at z=0 (bottom-anchored); everything else is
# built centered on the origin. Controls how `position` is interpreted when a
# part does not declare `position_mode` explicitly.
BASE_ANCHORED_SHAPES = {"tapered_extrude", "revolve_lathe", "extrude", "sweep"}


def emit(payload):
    print(RESULT_BEGIN)
    # ensure_ascii keeps stdout safe under any Windows pipe encoding.
    print(json.dumps(payload, ensure_ascii=True))
    print(RESULT_END)
    sys.stdout.flush()


def read_request():
    argv = sys.argv
    if "--" not in argv:
        raise RuntimeError("No request JSON file passed after '--'")
    idx = argv.index("--")
    if idx + 1 >= len(argv):
        raise RuntimeError("Missing request file path after '--'")
    with open(argv[idx + 1], "r", encoding="utf-8") as f:
        return json.load(f)


# ── Scene helpers ────────────────────────────────────────────────────────────


def reset_scene():
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    for block in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.textures,
        bpy.data.images,
        bpy.data.actions,
        bpy.data.armatures,
        bpy.data.cameras,
        bpy.data.lights,
        bpy.data.curves,
    ):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


def set_scene_units(scale=1.0):
    import bpy

    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"
    scene.unit_settings.scale_length = scale


def select_only(objects):
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    for o in objects:
        o.select_set(True)
    if objects:
        bpy.context.view_layer.objects.active = objects[0]


def import_any(path):
    import bpy

    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=path)
        else:
            bpy.ops.import_scene.obj(filepath=path)
    elif ext == ".stl":
        if hasattr(bpy.ops.wm, "stl_import"):
            bpy.ops.wm.stl_import(filepath=path)
        else:
            bpy.ops.import_mesh.stl(filepath=path)
    elif ext == ".ply":
        if hasattr(bpy.ops.wm, "ply_import"):
            bpy.ops.wm.ply_import(filepath=path)
        else:
            bpy.ops.import_mesh.ply(filepath=path)
    elif ext == ".dae":
        bpy.ops.wm.collada_import(filepath=path)
    elif ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=path)
        return list(bpy.data.objects)
    elif ext in (".usd", ".usda", ".usdc", ".usdz"):
        bpy.ops.wm.usd_import(filepath=path)
    else:
        raise ValueError(f"Unsupported import format: {ext}")
    return [o for o in bpy.data.objects if o not in before]


def export_any(path, selected_only=False, apply_modifiers=True):
    import bpy

    out_dir = os.path.dirname(os.path.abspath(path))
    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.export_scene.gltf(
            filepath=path,
            export_format="GLB" if ext == ".glb" else "GLTF_SEPARATE",
            use_selection=selected_only,
            export_apply=apply_modifiers,
            export_tangents=True,
            export_materials="EXPORT",
            export_yup=True,
        )
    elif ext == ".fbx":
        bpy.ops.export_scene.fbx(
            filepath=path,
            use_selection=selected_only,
            apply_unit_scale=True,
            bake_space_transform=True,
        )
    elif ext == ".obj":
        if hasattr(bpy.ops.wm, "obj_export"):
            bpy.ops.wm.obj_export(filepath=path, export_selected_objects=selected_only)
        else:
            bpy.ops.export_scene.obj(filepath=path, use_selection=selected_only)
    elif ext == ".stl":
        if hasattr(bpy.ops.wm, "stl_export"):
            bpy.ops.wm.stl_export(filepath=path, export_selected_objects=selected_only)
        else:
            bpy.ops.export_mesh.stl(filepath=path, use_selection=selected_only)
    elif ext in (".usd", ".usda", ".usdc", ".usdz"):
        bpy.ops.wm.usd_export(filepath=path, selected_objects_only=selected_only)
    else:
        raise ValueError(f"Unsupported export format: {ext}")
    return path


def mesh_objects():
    import bpy

    return [o for o in bpy.data.objects if o.type == "MESH"]


def _update_view():
    """Force a depsgraph evaluation. In background mode obj.matrix_world is
    only refreshed on update — reading it right after setting obj.location
    otherwise returns a stale matrix."""
    import bpy

    bpy.context.view_layer.update()


def world_bbox(objects):
    """World-space bounding box of the given objects as (min, max) lists."""
    from mathutils import Vector

    if not objects:
        return None
    _update_view()
    min_c = Vector((float("inf"),) * 3)
    max_c = Vector((float("-inf"),) * 3)
    for obj in objects:
        for corner in obj.bound_box:
            wc = obj.matrix_world @ Vector(corner)
            min_c.x = min(min_c.x, wc.x)
            min_c.y = min(min_c.y, wc.y)
            min_c.z = min(min_c.z, wc.z)
            max_c.x = max(max_c.x, wc.x)
            max_c.y = max(max_c.y, wc.y)
            max_c.z = max(max_c.z, wc.z)
    return (list(min_c), list(max_c))


def get_mesh_bounds(objects=None):
    """Aggregate metric bounds dict used by measure/gate code."""
    if objects is None:
        objects = mesh_objects()
    bb = world_bbox(objects)
    if bb is None:
        return None
    (mn, mx) = bb
    size = [mx[i] - mn[i] for i in range(3)]
    center = [(mn[i] + mx[i]) / 2.0 for i in range(3)]
    return {
        "min": mn,
        "max": mx,
        "dimensions": size,
        "center": center,
        "width_x": size[0],
        "depth_y": size[1],
        "height_z": size[2],
    }


def _apply_transforms(obj, location=True, rotation=True, scale=True):
    import bpy

    select_only([obj])
    bpy.ops.object.transform_apply(location=location, rotation=rotation, scale=scale)


def _recalc_normals(obj):
    import bpy

    select_only([obj])
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def _shade_auto_smooth(obj, angle_deg=40.0):
    import bpy

    select_only([obj])
    bpy.ops.object.shade_smooth()
    if hasattr(obj.data, "use_auto_smooth"):
        obj.data.use_auto_smooth = True
        obj.data.auto_smooth_angle = math.radians(angle_deg)
    else:
        try:
            bpy.ops.object.shade_auto_smooth(angle=math.radians(angle_deg))
        except Exception:
            pass


def _object_from_bmesh(bm, name):
    import bpy

    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(obj)
    select_only([obj])
    return obj


def _fill_boundary_loops(bm):
    """Fan-fill every boundary loop (open hole) in the bmesh."""
    boundary = [e for e in bm.edges if e.is_boundary]
    if not boundary:
        return 0
    remaining = set(boundary)
    filled = 0
    while remaining:
        start = next(iter(remaining))
        remaining.discard(start)
        loop_verts = [start.verts[0], start.verts[1]]
        while True:
            current = loop_verts[-1]
            nxt = None
            for e in current.link_edges:
                if e in remaining:
                    nxt = e
                    break
            if nxt is None:
                break
            remaining.discard(nxt)
            other = nxt.verts[0] if nxt.verts[1] is current else nxt.verts[1]
            if other is loop_verts[0]:
                break
            loop_verts.append(other)
        if len(loop_verts) >= 3:
            n = len(loop_verts)
            center = bm.verts.new(
                (
                    sum(v.co.x for v in loop_verts) / n,
                    sum(v.co.y for v in loop_verts) / n,
                    sum(v.co.z for v in loop_verts) / n,
                )
            )
            for i in range(n):
                try:
                    bm.faces.new((center, loop_verts[i], loop_verts[(i + 1) % n]))
                    filled += 1
                except ValueError:
                    pass
    return filled


# ── Shape builders (each returns an object at identity transform) ───────────


def _build_box(name, dims):
    import bpy

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (float(dims[0]), float(dims[1]), float(dims[2]))
    _apply_transforms(obj, location=False, rotation=False, scale=True)
    return obj


def _build_cylinder(name, dims, segments=32):
    import bpy

    radius = float(dims[0]) * 0.5
    depth = float(dims[2]) if len(dims) > 2 else float(dims[0])
    bpy.ops.mesh.primitive_cylinder_add(
        radius=radius, depth=depth, vertices=int(segments), location=(0, 0, 0)
    )
    obj = bpy.context.active_object
    obj.name = name
    return obj


def _build_tapered_cylinder(name, dims, top_scale, segments=32):
    import bpy

    bottom_r = float(dims[0]) * 0.5
    ts = float(top_scale[0]) if top_scale else 0.7
    depth = float(dims[2]) if len(dims) > 2 else float(dims[0])
    bpy.ops.mesh.primitive_cone_add(
        radius1=bottom_r,
        radius2=bottom_r * ts,
        depth=depth,
        vertices=int(segments),
        location=(0, 0, 0),
    )
    obj = bpy.context.active_object
    obj.name = name
    return obj


def _build_sphere(name, dims):
    import bpy

    radius = float(dims[0]) * 0.5
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, segments=32, ring_count=16, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = name
    if len(dims) >= 3 and (dims[0] != dims[1] or dims[1] != dims[2]):
        obj.scale = (1.0, float(dims[1]) / float(dims[0]), float(dims[2]) / float(dims[0]))
        _apply_transforms(obj, location=False, rotation=False, scale=True)
    return obj


def _build_cone(name, dims, segments=32):
    import bpy

    bpy.ops.mesh.primitive_cone_add(
        radius1=float(dims[0]) * 0.5,
        radius2=0.0,
        depth=float(dims[2]) if len(dims) > 2 else float(dims[0]),
        vertices=int(segments),
        location=(0, 0, 0),
    )
    obj = bpy.context.active_object
    obj.name = name
    return obj


def _build_torus(name, dims):
    import bpy

    major_r = float(dims[0]) * 0.5
    minor_r = float(dims[2]) * 0.5 if len(dims) > 2 else 0.05
    bpy.ops.mesh.primitive_torus_add(
        major_radius=major_r, minor_radius=minor_r, location=(0, 0, 0)
    )
    obj = bpy.context.active_object
    obj.name = name
    return obj


def _build_tapered_extrude(name, dims, top_scale):
    """Rectangular prism whose top face is scaled toward its center (legs, spokes)."""
    import bmesh

    width_x, depth_y, height_z = float(dims[0]), float(dims[1]), float(dims[2])
    sx, sy = (float(top_scale[0]), float(top_scale[1])) if top_scale else (0.7, 0.7)

    bm = bmesh.new()
    hw, hd = width_x * 0.5, depth_y * 0.5
    v0 = bm.verts.new((-hw, -hd, 0))
    v1 = bm.verts.new((hw, -hd, 0))
    v2 = bm.verts.new((hw, hd, 0))
    v3 = bm.verts.new((-hw, hd, 0))
    thw, thd = hw * sx, hd * sy
    v4 = bm.verts.new((-thw, -thd, height_z))
    v5 = bm.verts.new((thw, -thd, height_z))
    v6 = bm.verts.new((thw, thd, height_z))
    v7 = bm.verts.new((-thw, thd, height_z))

    bm.faces.new((v3, v2, v1, v0))
    bm.faces.new((v4, v5, v6, v7))
    bm.faces.new((v0, v1, v5, v4))
    bm.faces.new((v1, v2, v6, v5))
    bm.faces.new((v2, v3, v7, v6))
    bm.faces.new((v3, v0, v4, v7))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    return _object_from_bmesh(bm, name)


def _build_revolve(name, profile_points, segments=32):
    """Revolve a [[r, z], ...] profile around the Z axis. Capped and watertight.

    Profile points on the axis (r == 0) become a single shared vertex per point
    so the pole faces are clean triangles instead of degenerate quads."""
    import bmesh

    if not profile_points or len(profile_points) < 2:
        raise ValueError(f"revolve_lathe part '{name}' needs >= 2 profile_points [[r, z], ...]")
    for pt in profile_points:
        if len(pt) < 2 or float(pt[0]) < 0:
            raise ValueError(f"revolve_lathe part '{name}' has invalid profile point {pt} (need [r>=0, z])")

    bm = bmesh.new()
    segments = max(3, int(segments))
    n = len(profile_points)
    on_axis = [float(r) <= 1e-12 for r, _ in profile_points]
    axis_verts = [
        bm.verts.new((0.0, 0.0, float(z))) if a else None for a, (_, z) in zip(on_axis, profile_points)
    ]

    rings = []
    for seg in range(segments):
        theta = (math.pi * 2.0 / segments) * seg
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        ring = [
            axis_verts[i]
            if on_axis[i]
            else bm.verts.new((float(r) * cos_t, float(r) * sin_t, float(z)))
            for i, (r, z) in enumerate(profile_points)
        ]
        rings.append(ring)

    for s in range(segments):
        r0, r1 = rings[s], rings[(s + 1) % segments]
        for i in range(n - 1):
            a, b, c, d = r0[i], r0[i + 1], r1[i + 1], r1[i]
            if a is d and b is c:
                continue  # zero-width band along the axis
            if a is d:
                bm.faces.new((a, b, c))  # pole at profile[i]
            elif b is c:
                bm.faces.new((a, b, d))  # pole at profile[i+1]
            else:
                bm.faces.new((a, b, c, d))

    # Fan-fill any open tube ends (profile not starting/ending on the axis).
    _fill_boundary_loops(bm)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _object_from_bmesh(bm, name)


def _build_extrude(name, profile_points, height, top_scale=None):
    """Extrude a [[x, y], ...] polygon; optional taper toward the profile centroid."""
    import bmesh

    if not profile_points or len(profile_points) < 3:
        raise ValueError(f"extrude part '{name}' needs >= 3 profile_points [[x, y], ...]")
    height = float(height)
    taper = float(top_scale[0]) if top_scale else 1.0

    pts = [(float(p[0]), float(p[1])) for p in profile_points]
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)

    bm = bmesh.new()
    bottom = [bm.verts.new((x, y, 0.0)) for x, y in pts]
    top = [bm.verts.new(((x - cx) * taper + cx, (y - cy) * taper + cy, height)) for x, y in pts]
    n = len(pts)
    bm.faces.new(list(reversed(bottom)))
    bm.faces.new(top)
    for i in range(n):
        bm.faces.new((bottom[i], bottom[(i + 1) % n], top[(i + 1) % n], top[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _object_from_bmesh(bm, name)


def _build_sweep(name, path_points, dims):
    """Sweep a circular or rectangular section along a 3D poly path (cables, tubes)."""
    import bpy

    if not path_points or len(path_points) < 2:
        raise ValueError(f"sweep part '{name}' needs >= 2 path_points [[x, y, z], ...]")

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("POLY")
    spline.points.add(len(path_points) - 1)
    for p, co in zip(spline.points, path_points):
        p.co = (float(co[0]), float(co[1]), float(co[2]), 1.0)

    # Circular section when width == height (or height unset); rectangular otherwise.
    use_rect = len(dims) >= 2 and float(dims[1]) > 0 and abs(float(dims[1]) - float(dims[0])) > 1e-9
    if use_rect:
        w, h = float(dims[0]), float(dims[1])
        bevel_curve = bpy.data.curves.new(name + "_section", type="CURVE")
        bevel_curve.dimensions = "2D"
        sp = bevel_curve.splines.new("POLY")
        rect = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
        sp.points.add(len(rect) - 1)
        for p, (x, y) in zip(sp.points, rect):
            p.co = (x, y, 0.0, 1.0)
        sp.use_cyclic_u = True
        bevel_obj = bpy.data.objects.new(name + "_section", bevel_curve)
        bpy.context.collection.objects.link(bevel_obj)
        curve.bevel_object = bevel_obj
    else:
        curve.bevel_depth = float(dims[0]) * 0.5

    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    select_only([obj])
    bpy.ops.object.convert(target="MESH")
    obj.name = name

    # Close the open tube ends.
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    _fill_boundary_loops(bm)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()

    # Remove the helper bevel object if we made one.
    bevel_obj = bpy.data.objects.get(name + "_section")
    if bevel_obj:
        bpy.data.objects.remove(bevel_obj, do_unlink=True)
    return obj


# ── Assembly modifiers (world-space, applied and joined) ─────────────────────


def _clone_with_matrix(obj, matrix):
    import bpy

    _update_view()  # obj.matrix_world may be stale after a location change
    dup = obj.copy()
    dup.data = obj.data.copy()
    dup.matrix_world = matrix @ obj.matrix_world
    bpy.context.collection.objects.link(dup)
    return dup


def _join_objects(objects, name):
    import bpy

    for o in objects:
        _apply_transforms(o, location=True, rotation=True, scale=True)
    select_only(objects)
    bpy.ops.object.join()
    joined = bpy.context.active_object
    joined.name = name
    _recalc_normals(joined)
    return joined


def apply_radial_array(obj, count, center=(0.0, 0.0, 0.0), axis="z"):
    from mathutils import Matrix, Vector

    count = max(1, int(count))
    if count < 2:
        return obj
    axis_vec = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}.get(axis, (0, 0, 1))
    t = Matrix.Translation(Vector(center))
    clones = []
    for i in range(1, count):
        rot = Matrix.Rotation((math.pi * 2.0 / count) * i, 4, Vector(axis_vec))
        clones.append(_clone_with_matrix(obj, t @ rot @ t.inverted()))
    return _join_objects([obj] + clones, obj.name)


def apply_linear_array(obj, count, direction, spacing):
    from mathutils import Matrix, Vector

    count = max(1, int(count))
    if count < 2:
        return obj
    d = Vector([float(v) for v in direction]).normalized()
    clones = []
    for i in range(1, count):
        tr = Matrix.Translation(d * (float(spacing) * i))
        clones.append(_clone_with_matrix(obj, tr))
    return _join_objects([obj] + clones, obj.name)


def apply_world_mirror(obj, axis):
    from mathutils import Matrix

    idx = {"x": 0, "y": 1, "z": 2}.get(axis, 0)
    m = Matrix.Identity(4)
    m[idx][idx] = -1.0
    dup = _clone_with_matrix(obj, m)
    return _join_objects([obj, dup], obj.name)


def apply_boolean(target, tool, operation="difference"):
    import bpy

    mod = target.modifiers.new(name="ThreedBoolean", type="BOOLEAN")
    mod.operation = str(operation).upper()
    mod.solver = "EXACT"
    mod.object = tool
    select_only([target])
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(tool, do_unlink=True)
    # Purge the orphaned tool mesh so it cannot leak into exports.
    for me in list(bpy.data.meshes):
        if me.users == 0:
            bpy.data.meshes.remove(me)


# ── Materials ────────────────────────────────────────────────────────────────


def _set_bsdf_input(bsdf, names, value):
    for n in names:
        if n in bsdf.inputs:
            bsdf.inputs[n].default_value = value
            return


def build_flat_pbr_material(name, color=None, roughness=0.5, metallic=0.0, transmission=0.0, ior=1.45):
    """Constant-value Principled BSDF. These values export to glTF correctly."""
    import bpy

    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    base_col = list(color) if color else [0.8, 0.8, 0.8]
    if len(base_col) == 3:
        base_col.append(1.0)
    bsdf.inputs["Base Color"].default_value = base_col
    bsdf.inputs["Roughness"].default_value = float(roughness)
    bsdf.inputs["Metallic"].default_value = float(metallic)
    _set_bsdf_input(bsdf, ["Transmission Weight", "Transmission"], float(transmission))
    _set_bsdf_input(bsdf, ["IOR"], float(ior))
    return mat


def build_procedural_pbr_material(name, preset=None, color=None, roughness=0.5, metallic=0.0, transmission=0.0):
    """Procedural node shaders for high-quality previews. NOTE: node-driven
    inputs do not survive glTF export — pair with the bake_materials op when
    exporting, or use flat materials."""
    import bpy

    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    tree = mat.node_tree
    nodes = tree.nodes
    links = tree.links
    nodes.clear()

    output = nodes.new(type="ShaderNodeOutputMaterial")
    output.location = (400, 0)
    bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    base_col = list(color) if color else [0.8, 0.8, 0.8]
    if len(base_col) == 3:
        base_col.append(1.0)
    bsdf.inputs["Roughness"].default_value = float(roughness)
    bsdf.inputs["Metallic"].default_value = float(metallic)

    preset_name = (preset or "").lower()

    if preset_name in ("oak_wood", "walnut_wood", "wood"):
        tex_coord = nodes.new(type="ShaderNodeTexCoord")
        tex_coord.location = (-800, 0)
        mapping = nodes.new(type="ShaderNodeMapping")
        mapping.location = (-600, 0)
        mapping.inputs["Scale"].default_value = (1.0, 1.0, 15.0)
        links.new(tex_coord.outputs["Object"], mapping.inputs["Vector"])
        wave = nodes.new(type="ShaderNodeTexWave")
        wave.location = (-400, 100)
        wave.wave_type = "RINGS"
        wave.rings_direction = "Z"
        wave.inputs["Scale"].default_value = 8.0
        wave.inputs["Distortion"].default_value = 2.5
        links.new(mapping.outputs["Vector"], wave.inputs["Vector"])
        ramp = nodes.new(type="ShaderNodeValToRGB")
        ramp.location = (-200, 100)
        if preset_name == "walnut_wood":
            ramp.color_ramp.elements[0].color = (0.18, 0.10, 0.06, 1.0)
            ramp.color_ramp.elements[1].color = (0.32, 0.20, 0.13, 1.0)
        else:
            ramp.color_ramp.elements[0].color = (0.50, 0.32, 0.18, 1.0)
            ramp.color_ramp.elements[1].color = (0.70, 0.50, 0.32, 1.0)
        links.new(wave.outputs["Fac"], ramp.inputs["Fac"])
        links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])

    elif preset_name in ("leather_black", "leather_brown", "leather"):
        tex_coord = nodes.new(type="ShaderNodeTexCoord")
        tex_coord.location = (-600, 0)
        voronoi = nodes.new(type="ShaderNodeTexVoronoi")
        voronoi.location = (-400, -100)
        voronoi.inputs["Scale"].default_value = 120.0
        links.new(tex_coord.outputs["Object"], voronoi.inputs["Vector"])
        bump = nodes.new(type="ShaderNodeBump")
        bump.location = (-200, -100)
        bump.inputs["Strength"].default_value = 0.15
        links.new(voronoi.outputs["Distance"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
        bsdf.inputs["Base Color"].default_value = base_col

    elif preset_name in ("frosted_glass", "glass"):
        bsdf.inputs["Base Color"].default_value = base_col
        _set_bsdf_input(bsdf, ["Transmission Weight", "Transmission"], float(transmission or 0.88))
        _set_bsdf_input(bsdf, ["IOR"], 1.45)

    else:
        bsdf.inputs["Base Color"].default_value = base_col

    return mat


def apply_material(objects, mat_spec):
    if not mat_spec:
        return None
    name = mat_spec.get("name", "ThreedMaterial")
    if mat_spec.get("procedural"):
        mat = build_procedural_pbr_material(
            name=name,
            preset=mat_spec.get("preset"),
            color=mat_spec.get("color"),
            roughness=mat_spec.get("roughness", 0.5),
            metallic=mat_spec.get("metallic", 0.0),
            transmission=mat_spec.get("transmission", 0.0),
        )
    else:
        mat = build_flat_pbr_material(
            name=name,
            color=mat_spec.get("color"),
            roughness=mat_spec.get("roughness", 0.5),
            metallic=mat_spec.get("metallic", 0.0),
            transmission=mat_spec.get("transmission", 0.0),
        )
    for obj in objects:
        if obj.type == "MESH":
            obj.data.materials.clear()
            obj.data.materials.append(mat)
    return mat.name


# ── Spec building ────────────────────────────────────────────────────────────


def _build_shape(part):
    """Build one part's base geometry. Returns the object at identity transform."""
    name = part.get("name", "part")
    shape = str(part.get("shape", "box")).lower()
    dims = part.get("dimensions") or [1.0, 1.0, 1.0]
    dims = [float(d) for d in dims]
    segments = part.get("segments") or 32

    if shape in ("box", "rounded_box"):
        return _build_box(name, dims)
    if shape == "cylinder":
        return _build_cylinder(name, dims, segments)
    if shape == "tapered_cylinder":
        return _build_tapered_cylinder(name, dims, part.get("top_scale"), segments)
    if shape == "sphere":
        return _build_sphere(name, dims)
    if shape == "cone":
        return _build_cone(name, dims, segments)
    if shape == "torus":
        return _build_torus(name, dims)
    if shape == "tapered_extrude":
        return _build_tapered_extrude(name, dims, part.get("top_scale"))
    if shape == "revolve_lathe":
        return _build_revolve(name, part.get("profile_points"), segments)
    if shape == "extrude":
        height = dims[2] if len(dims) > 2 else 0.1
        return _build_extrude(name, part.get("profile_points"), height, part.get("top_scale"))
    if shape == "sweep":
        return _build_sweep(name, part.get("path_points"), dims)
    raise ValueError(f"Unknown shape '{shape}' for part '{name}'")


def _place_part(obj, part):
    """Apply rotation, then position. `position` is the part center for
    center-anchored shapes and the bottom-center for base-anchored shapes."""
    pos = [float(v) for v in part.get("position", [0.0, 0.0, 0.0])]
    rot = [float(v) for v in part.get("rotation", [0.0, 0.0, 0.0])]

    if any(abs(r) > 1e-9 for r in rot):
        obj.rotation_euler = (math.radians(rot[0]), math.radians(rot[1]), math.radians(rot[2]))
        _apply_transforms(obj, location=False, rotation=True, scale=False)

    shape = str(part.get("shape", "box")).lower()
    mode = part.get("position_mode") or ("base" if shape in BASE_ANCHORED_SHAPES else "center")

    bb = world_bbox([obj])
    if bb is None:
        raise RuntimeError(f"Part '{part.get('name')}' produced no geometry")
    (mn, mx) = bb
    center = [(mn[i] + mx[i]) / 2.0 for i in range(3)]
    if mode == "base":
        offset = (pos[0] - center[0], pos[1] - center[1], pos[2] - mn[2])
    else:
        offset = (pos[0] - center[0], pos[1] - center[1], pos[2] - center[2])
    obj.location = (
        obj.location.x + offset[0],
        obj.location.y + offset[1],
        obj.location.z + offset[2],
    )


def op_build_from_spec(params):
    import bpy

    reset_scene()
    set_scene_units(1.0)

    spec = params.get("spec", {})
    parts = spec.get("parts", [])
    if not parts:
        return {"success": False, "error": "No parts defined in ObjectSpec"}

    warnings = []
    built = {}
    obj_list = []

    # Pass 1: build geometry, detail modifiers, positioning, assembly modifiers.
    for part in parts:
        name = part.get("name", "part")
        method = str(part.get("method", "parametric")).lower()

        if method == "script":
            code = part.get("code", "")
            if not code:
                warnings.append(f"Part '{name}' uses script method but has no code")
                continue
            import bpy

            ns = {"bpy": bpy, "RESULT": None}
            exec(code, ns, ns)  # noqa: S102 — explicit, user-authorised
            if name in bpy.data.objects:
                built[name] = bpy.data.objects[name]
                obj_list.append(built[name])
            else:
                warnings.append(f"Script part '{name}' created no object named '{name}'")
            continue

        if method == "image_to_3d" or str(part.get("shape", "")).lower() == "organic":
            # 'organic' shapes are only ever neural — route them through the
            # same import path so a spec without a generated mesh degrades to
            # a warning instead of an 'Unknown shape' build error.
            mesh_path = part.get("mesh_path")
            if not mesh_path or not os.path.exists(str(mesh_path)):
                warnings.append(
                    f"Part '{name}' is image_to_3d but has no generated mesh_path yet — skipped"
                )
                continue
            imported = import_any(str(mesh_path))
            meshes = [o for o in imported if o.type == "MESH"]
            if not meshes:
                warnings.append(f"image_to_3d part '{name}' imported no meshes")
                continue
            if len(meshes) > 1:
                obj = _join_objects(meshes, name)
            else:
                obj = meshes[0]
                obj.name = name
            target = part.get("target_size") or part.get("dimensions")
            if target:
                _scale_object_to_bounds(obj, [float(v) for v in target])
            _place_part(obj, part)
            built[name] = obj
            obj_list.append(obj)
            continue

        obj = _build_shape(part)

        mods = part.get("modifiers") or {}
        if mods.get("bevel"):
            bev = obj.modifiers.new(name="ThreedBevel", type="BEVEL")
            bev.width = float(mods["bevel"].get("width", 0.005))
            bev.segments = int(mods["bevel"].get("segments", 3))
            bev.limit_method = "ANGLE"
            select_only([obj])
            bpy.ops.object.modifier_apply(modifier=bev.name)
        if mods.get("subdivision"):
            sub = obj.modifiers.new(name="ThreedSubdiv", type="SUBSURF")
            sub.levels = int(mods["subdivision"].get("levels", 1))
            select_only([obj])
            bpy.ops.object.modifier_apply(modifier=sub.name)

        _place_part(obj, part)

        if mods.get("radial_array"):
            obj = apply_radial_array(
                obj,
                mods["radial_array"].get("count", 5),
                center=mods["radial_array"].get("center", (0.0, 0.0, 0.0)),
                axis=mods["radial_array"].get("axis", "z"),
            )
        if mods.get("linear_array"):
            la = mods["linear_array"]
            obj = apply_linear_array(
                obj,
                la.get("count", 2),
                la.get("direction", [0, 0, 1]),
                la.get("spacing", 0.1),
            )
        if mods.get("mirror"):
            obj = apply_world_mirror(obj, mods["mirror"].get("axis", "x"))

        obj.name = name
        if obj.data is not None:
            obj.data.name = name  # glTF mesh names come from the datablock
        built[name] = obj
        obj_list.append(obj)

    # Pass 2: cross-part booleans.
    for part in parts:
        mods = part.get("modifiers") or {}
        boo = mods.get("boolean")
        if not boo:
            continue
        target_name = part.get("name")
        tool_name = boo.get("target_part")
        if target_name not in built:
            warnings.append(f"Boolean skipped: target part '{target_name}' missing")
            continue
        if tool_name not in built:
            warnings.append(f"Boolean skipped: tool part '{tool_name}' missing")
            continue
        tool_obj = built[tool_name]
        apply_boolean(
            built[target_name],
            tool_obj,
            boo.get("operation", "difference"),
        )
        del built[tool_name]
        # tool_obj's RNA struct is freed by apply_boolean — filter by identity,
        # touching .name on the removed object would raise ReferenceError.
        obj_list = [o for o in obj_list if o is not tool_obj]

    # Pass 3: ground-contact constraint — snap declared parts to Z = 0.
    for constraint in spec.get("constraints", []) or []:
        if str(constraint.get("type", "")).lower() != "ground_contact":
            continue
        for pname in constraint.get("parts", []):
            obj = built.get(pname)
            if not obj:
                warnings.append(f"ground_contact part '{pname}' not found")
                continue
            bb = world_bbox([obj])
            if bb:
                obj.location.z -= bb[0][2]

    # Pass 4: shading + materials + UVs.
    for part in parts:
        obj = built.get(part.get("name"))
        if not obj:
            continue
        if part.get("smooth_shade", False):
            _shade_auto_smooth(obj)
        if part.get("material"):
            apply_material([obj], part["material"])

    if params.get("generate_uvs", True):
        for obj in obj_list:
            if obj.type != "MESH" or not obj.data.polygons:
                continue
            select_only([obj])
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.02)
            bpy.ops.object.mode_set(mode="OBJECT")

    # Assembly-level ground normalization: whole model sits on Z = 0.
    if params.get("center_origin_bottom", True) and obj_list:
        bb = world_bbox(obj_list)
        if bb:
            cx = (bb[0][0] + bb[1][0]) / 2.0
            cy = (bb[0][1] + bb[1][1]) / 2.0
            dz = -bb[0][2]
            for o in obj_list:
                o.location.x -= cx
                o.location.y -= cy
                o.location.z += dz

    output_path = params.get("output_path")
    if output_path:
        export_any(str(output_path))

    final_bounds = get_mesh_bounds(obj_list)
    return {
        "success": True,
        "parts_created": len(obj_list),
        "part_names": [o.name for o in obj_list],
        "warnings": warnings,
        "overall_bounds": final_bounds,
        "output_path": str(output_path) if output_path else None,
    }


def _scale_object_to_bounds(obj, target_axes):
    """Scale a single object so its world bbox matches the target per-axis sizes."""
    bb = world_bbox([obj])
    if bb is None:
        return
    (mn, mx) = bb
    for i in range(3):
        current = mx[i] - mn[i]
        t = target_axes[i] if i < len(target_axes) else None
        target = float(t) if t is not None else current
        if current > 1e-9 and target > 0:
            factor = target / current
            if i == 0:
                obj.scale.x *= factor
            elif i == 1:
                obj.scale.y *= factor
            else:
                obj.scale.z *= factor
    _apply_transforms(obj, location=False, rotation=False, scale=True)


# ── Measurement / rendering / inspection ops ────────────────────────────────


def op_measure(params):
    model_path = params.get("model_path")
    if model_path:
        reset_scene()
        import_any(str(model_path))
    else:
        import bpy  # noqa: F401 — measure may run on the current scene

    meshes = mesh_objects()
    if not meshes:
        return {"success": False, "error": "No mesh objects in scene"}

    overall = get_mesh_bounds(meshes)
    parts = {}
    total_verts = 0
    total_faces = 0

    for m in meshes:
        b = get_mesh_bounds([m])
        v_c = len(m.data.vertices)
        f_c = len(m.data.polygons)
        total_verts += v_c
        total_faces += f_c
        parts[m.name] = {
            "dimensions": b["dimensions"],
            "center": b["center"],
            "min": b["min"],
            "max": b["max"],
            "bottom_z": b["min"][2],
            "top_z": b["max"][2],
            "vertices": v_c,
            "faces": f_c,
        }

    return {
        "success": True,
        "overall": overall,
        "parts": parts,
        "total_vertices": total_verts,
        "total_faces": total_faces,
        "units": "meters",
    }


def _count_face_kinds(meshes):
    """(tris, quads, ngons, triangle_equivalent, faces_total, vertices) across
    mesh objects. triangle_equivalent = Σ(max(verts−2, 0)) per face — the
    triangulated polycount a client validator counts."""
    tris = quads = ngons = tri_eq = faces = verts = 0
    for m in meshes:
        for p in m.data.polygons:
            n = len(p.vertices)
            faces += 1
            if n == 3:
                tris += 1
            elif n == 4:
                quads += 1
            else:
                ngons += 1
            tri_eq += max(n - 2, 0)
        verts += len(m.data.vertices)
    return tris, quads, ngons, tri_eq, faces, verts


def _topology_diagnostics(mesh_obj):
    """(loose_vertices, loose_edges, boundary_edges, nonmanifold_edges) for
    one mesh object. Loose = not part of any face; boundary = one face;
    non-manifold = shared by more than two faces."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(mesh_obj.data)
    loose_v = sum(1 for v in bm.verts if not v.link_faces)
    loose_e = boundary = nonmanifold = 0
    for e in bm.edges:
        n = len(e.link_faces)
        if n == 0:
            loose_e += 1
        elif n == 1:
            boundary += 1
        elif n > 2:
            nonmanifold += 1
    bm.free()
    return loose_v, loose_e, boundary, nonmanifold


def op_count_ngons(params):
    """Count faces with more than 4 vertices (client n-gon gate)."""
    model_path = params.get("model_path")
    if not model_path:
        raise ValueError("model_path is required")
    reset_scene()
    import_any(str(model_path))
    meshes = mesh_objects()
    if not meshes:
        return {"success": False, "error": "No mesh objects in scene"}
    ngons = sum(1 for m in meshes for p in m.data.polygons if len(p.vertices) > 4)
    return {
        "success": True,
        "model_path": str(model_path),
        "ngon_count": ngons,
        "objects": len(meshes),
        "units": "count",
    }


def op_topology_report(params):
    """Full topology + bounds report for a model file: tri/quad/ngon counts,
    triangle-equivalent polycount, loose geometry, non-manifold edges, and
    overall world-space bounds in metres. Feeds the client gates
    (n-gons, polycount, dimensions, orientation)."""
    model_path = params.get("model_path")
    if not model_path:
        raise ValueError("model_path is required")
    reset_scene()
    import_any(str(model_path))
    meshes = mesh_objects()
    if not meshes:
        return {"success": False, "error": "No mesh objects in scene"}
    tris, quads, ngons, tri_eq, faces, verts = _count_face_kinds(meshes)
    loose_v = loose_e = boundary = nonmanifold = 0
    for m in meshes:
        lv, le, be, nm = _topology_diagnostics(m)
        loose_v += lv
        loose_e += le
        boundary += be
        nonmanifold += nm
    bounds = get_mesh_bounds(meshes)
    return {
        "success": True,
        "model_path": str(model_path),
        "units": "meters",
        "objects": len(meshes),
        "vertices": verts,
        "faces_total": faces,
        "triangles": tris,
        "quads": quads,
        "ngons": ngons,
        "triangle_equivalent": tri_eq,
        "loose_vertices": loose_v,
        "loose_edges": loose_e,
        "boundary_edges": boundary,
        "nonmanifold_edges": nonmanifold,
        "bounds": bounds,
    }


def op_export_fbx(params):
    """Binary FBX export with explicit axis/unit control (client deliverable).

    Defaults follow the FBX-standard convention: axis_up="Y",
    axis_forward="-Z" (Blender's Z-up internal space converted to Y-up),
    which is what third-party consumers (Babylon, Unity, FBX SDK) expect.
    NOTE (owner amendment 1, T2): a Blender re-import of this file is
    self-consistent even when the file is wrong for a third party — the
    caller MUST verify the written convention independently via
    src/client/fbx_inspect.py. FBX version is whatever Blender writes
    (7.4 binary; readable by the FBX 2020 SDK).
    When `input` is given, the source is imported first — importing a GLB
    TRIANGULATES (glTF stores triangles only), so an FBX produced from a GLB
    is fully triangulated; exporting a live quad scene instead preserves
    quads AND n-gons (verified empirically)."""
    out_path = params.get("path")
    if not out_path:
        raise ValueError("path is required")
    if params.get("input"):
        reset_scene()
        import_any(str(params["input"]))
    import bpy

    axis_up = str(params.get("axis_up", "Y"))
    axis_forward = str(params.get("axis_forward", "-Z"))
    apply_unit_scale = bool(params.get("apply_unit_scale", True))
    bpy.ops.export_scene.fbx(
        filepath=str(out_path),
        axis_up=axis_up,
        axis_forward=axis_forward,
        apply_unit_scale=apply_unit_scale,
        bake_space_transform=True,
        use_selection=False,
        object_types={"MESH"},
    )
    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    return {
        "success": True,
        "path": str(out_path),
        "axis_up": axis_up,
        "axis_forward": axis_forward,
        "apply_unit_scale": apply_unit_scale,
        "file_size": size,
        "blender_version": f"Blender {bpy.app.version_string}",
    }


def _write_usdz_zip(usda_path, usdz_path):
    """USDZ fallback package: uncompressed zip (ZIP_STORED) whose single
    default layer is 64-byte aligned per the USDZ spec."""
    import zipfile

    name = os.path.basename(usda_path)
    with open(usda_path, "rb") as f:
        data = f.read()
    with zipfile.ZipFile(usdz_path, "w", compression=zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo(name)
        # local file header = 30 bytes + name + extra; pad `extra` so the
        # data starts on a 64-byte boundary
        pad = (-(30 + len(name.encode("utf-8")))) % 64
        info.extra = b"\x00" * pad
        zf.writestr(info, data)


def op_export_usdz(params):
    """USDZ export. Empirical question (brief T2): does Blender 4.5's USD
    exporter write .usdz directly? Try the direct write first; if the file
    comes back missing or empty, fall back to exporting .usda and packaging
    it as an uncompressed, 64-byte-aligned zip. Reports which method was
    used so qa_report.json can record it."""
    out_path = params.get("path")
    if not out_path:
        raise ValueError("path is required")
    if params.get("input"):
        reset_scene()
        import_any(str(params["input"]))
    import bpy

    method = "direct"
    direct_error = ""
    try:
        bpy.ops.wm.usd_export(filepath=str(out_path), selected_objects_only=False)
    except Exception as e:  # noqa: BLE001 — fall back and report
        direct_error = str(e)[:300]
        method = "zip-fallback"
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        method = "zip-fallback"
        base = os.path.splitext(out_path)[0]
        usda = base + ".usda"
        bpy.ops.wm.usd_export(filepath=usda, selected_objects_only=False)
        _write_usdz_zip(usda, usdz_path=out_path)
        if os.path.exists(usda):
            try:
                os.remove(usda)
            except OSError:
                pass
    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    return {
        "success": True,
        "path": str(out_path),
        "method": method,
        "direct_error": direct_error,
        "file_size": size,
        "blender_version": f"Blender {bpy.app.version_string}",
    }


def setup_studio_lighting():
    import bpy

    for obj in list(bpy.data.objects):
        if obj.type in ("LIGHT", "CAMERA"):
            bpy.data.objects.remove(obj, do_unlink=True)

    rigs = [
        ("KeyLight", "SUN", 3.5, (45, 0, 45), (4.0, -4.0, 5.0)),
        ("FillLight", "SUN", 1.5, (55, 0, -35), (-4.0, -3.0, 3.0)),
        ("RimLight", "SUN", 2.0, (-45, 0, 0), (0.0, 5.0, 4.0)),
    ]
    for name, ltype, energy, rot, loc in rigs:
        light_data = bpy.data.lights.new(name=name, type=ltype)
        light_data.energy = energy
        light_obj = bpy.data.objects.new(name=name, object_data=light_data)
        light_obj.location = loc
        light_obj.rotation_euler = (math.radians(rot[0]), math.radians(rot[1]), math.radians(rot[2]))
        bpy.context.collection.objects.link(light_obj)


def frame_camera_ortho(cam, bounds, view="iso"):
    from mathutils import Euler, Vector

    center = Vector(bounds["center"])
    dims = Vector(bounds["dimensions"])
    max_dim = max(dims.x, dims.y, dims.z, 0.1)
    dist = max_dim * 2.4

    if view == "front":
        cam.location = Vector((center.x, center.y - dist, center.z))
        cam.rotation_euler = Euler((math.radians(90), 0, 0), "XYZ")
        ortho_scale = max(dims.x, dims.z)
    elif view == "side":
        cam.location = Vector((center.x + dist, center.y, center.z))
        cam.rotation_euler = Euler((math.radians(90), 0, math.radians(90)), "XYZ")
        ortho_scale = max(dims.y, dims.z)
    elif view == "top":
        cam.location = Vector((center.x, center.y, center.z + dist))
        cam.rotation_euler = Euler((0, 0, 0), "XYZ")
        ortho_scale = max(dims.x, dims.y)
    else:  # iso
        offset = Vector((dist * 0.7, -dist * 0.7, dist * 0.6))
        cam.location = center + offset
        direction = center - cam.location
        cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        ortho_scale = max_dim

    cam.data.type = "ORTHO"
    cam.data.ortho_scale = ortho_scale * 1.15


def op_render_views(params):
    model_path = params.get("model_path")
    if model_path:
        reset_scene()
        import_any(str(model_path))
    else:
        import bpy  # noqa: F401 — render the current scene

    meshes = mesh_objects()
    if not meshes:
        return {"success": False, "error": "No meshes to render"}

    bounds = get_mesh_bounds(meshes)
    setup_studio_lighting()

    import bpy

    cam_data = bpy.data.cameras.new("RenderCamera")
    cam_data.lens = 50
    cam = bpy.data.objects.new("RenderCamera", cam_data)
    bpy.context.collection.objects.link(cam)
    bpy.context.scene.camera = cam

    out_dir = params.get("output_dir", "renders")
    os.makedirs(out_dir, exist_ok=True)
    prefix = params.get("prefix", "view")
    views = params.get("views", ["front", "side", "top", "iso"])
    resolution = params.get("resolution", [1024, 1024])
    if isinstance(resolution, int):
        resolution = [resolution, resolution]

    scene = bpy.context.scene
    scene.render.resolution_x = int(resolution[0])
    scene.render.resolution_y = int(resolution[1])
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = True

    rendered_files = {}
    for v in views:
        frame_camera_ortho(cam, bounds, view=str(v))
        file_path = os.path.abspath(os.path.join(out_dir, f"{prefix}_{v}.png"))
        scene.render.filepath = file_path
        bpy.ops.render.render(write_still=True)
        rendered_files[str(v)] = file_path

    return {
        "success": True,
        "views": rendered_files,
        "resolution": resolution,
    }


def describe_scene():
    import bpy

    objects = []
    for o in bpy.data.objects:
        entry = {
            "name": o.name,
            "type": o.type,
            "location": list(o.location),
            "rotation_euler": list(o.rotation_euler),
            "scale": list(o.scale),
        }
        if o.type == "MESH":
            entry["vertices"] = len(o.data.vertices)
            entry["polygons"] = len(o.data.polygons)
            entry["materials"] = [m.name for m in o.data.materials if m]
            entry["uv_layers"] = [uv.name for uv in o.data.uv_layers]
            bb = get_mesh_bounds([o])
            if bb:
                entry["dimensions"] = bb["dimensions"]
        objects.append(entry)
    return {
        "objects": objects,
        "materials": [m.name for m in bpy.data.materials],
        "actions": [a.name for a in bpy.data.actions],
    }


def op_inspect(params):
    reset_scene()
    import_any(str(params["input"]))
    return {"success": True, "scene": describe_scene()}


def op_import_model(params):
    reset_scene()
    imported = import_any(str(params["path"]))
    return {
        "success": True,
        "imported": [o.name for o in imported],
        "bounds": get_mesh_bounds([o for o in imported if o.type == "MESH"]),
    }


def op_export_model(params):
    if params.get("input"):
        reset_scene()
        import_any(str(params["input"]))
    path = export_any(str(params["path"]))
    return {"success": True, "path": path}


def op_convert(params):
    reset_scene()
    import_any(str(params["input"]))
    path = export_any(str(params["output"]), apply_modifiers=params.get("apply_modifiers", True))
    return {"success": True, "path": path, "bounds": get_mesh_bounds()}


def op_info(params):
    import bpy

    return {
        "success": True,
        "blender_version": f"Blender {bpy.app.version_string}",
        "version_tuple": list(bpy.app.version),
        "binary": bpy.app.binary_path,
        "objects_count": len(bpy.data.objects),
        "meshes_count": len(bpy.data.meshes),
        "materials_count": len(bpy.data.materials),
    }


def op_run_script(params):
    """Escape hatch: run agent-authored Python. The script may set RESULT to a
    JSON-serializable value; optional `input` model is loaded first and
    `output` exports the scene after."""
    reset_scene()
    import bpy
    import bmesh

    if params.get("input"):
        import_any(str(params["input"]))

    code = params.get("code", "")
    if not code:
        return {"success": False, "error": "No code provided"}

    namespace = {
        "bpy": bpy,
        "bmesh": bmesh,
        "math": math,
        "os": os,
        "RESULT": None,
        "get_mesh_bounds": get_mesh_bounds,
        "mesh_objects": mesh_objects,
        "select_only": select_only,
        "world_bbox": world_bbox,
    }
    exec(code, namespace, namespace)  # noqa: S102 — explicit, user-authorised

    result = {"success": True, "result": namespace.get("RESULT")}
    if params.get("output"):
        result["output"] = export_any(str(params["output"]))
    result["scene"] = describe_scene()
    return result


# ── Cleanup chain ops (ported from the proven OrianBuilder harness) ─────────


def op_decimate(params):
    import bpy

    reset_scene()
    import_any(str(params["input"]))
    ratio = float(params.get("ratio", 0.5))
    for obj in mesh_objects():
        mod = obj.modifiers.new(name="ThreedDecimate", type="DECIMATE")
        mod.ratio = max(0.01, min(1.0, ratio))
        select_only([obj])
        bpy.ops.object.modifier_apply(modifier=mod.name)
    out = export_any(str(params["output"]))
    return {"success": True, "path": out, "ratio": ratio, "bounds": get_mesh_bounds()}


def op_generate_uvs(params):
    import bpy

    reset_scene()
    import_any(str(params["input"]))
    for obj in mesh_objects():
        select_only([obj])
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(
            angle_limit=math.radians(float(params.get("angle_limit", 66.0))),
            island_margin=float(params.get("island_margin", 0.02)),
        )
        bpy.ops.object.mode_set(mode="OBJECT")
    out = export_any(str(params["output"]))
    return {"success": True, "path": out}


def op_scale_to_exact_bounds(params):
    """Rescale so the world bounding box hits exact target sizes.
    `target_axes` = {"x": m, "y": m, "z": m} for anisotropic, or `target_size`
    + `axis` for uniform scaling of the largest dimension."""
    reset_scene()
    import_any(str(params["input"]))

    target_axes = params.get("target_axes")
    uniform = params.get("target_size")
    axis_idx = {"x": 0, "y": 1, "z": 2}.get(params.get("axis", "z"), 2)

    for obj in mesh_objects():
        if target_axes:
            _scale_object_to_bounds(obj, [target_axes.get("x"), target_axes.get("y"), target_axes.get("z")])
        elif uniform:
            bb = world_bbox([obj])
            if bb:
                current = bb[1][axis_idx] - bb[0][axis_idx]
                if current > 1e-9:
                    factor = float(uniform) / current
                    obj.scale = (obj.scale.x * factor, obj.scale.y * factor, obj.scale.z * factor)
                    _apply_transforms(obj, location=False, rotation=False, scale=True)
    out = export_any(str(params["output"]))
    return {"success": True, "path": out, "bounds": get_mesh_bounds()}


def op_center_origin(params):
    import bpy

    reset_scene()
    import_any(str(params["input"]))
    for obj in mesh_objects():
        select_only([obj])
        bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
        bb = world_bbox([obj])
        if bb and params.get("mode", "bottom") == "bottom":
            half = (bb[1][2] - bb[0][2]) / 2.0
            bpy.context.scene.cursor.location = (obj.location.x, obj.location.y, obj.location.z - half)
            bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
            bpy.context.scene.cursor.location = (0, 0, 0)
        obj.location = (0, 0, 0)
    out = export_any(str(params["output"]))
    return {"success": True, "path": out, "bounds": get_mesh_bounds()}


def op_apply_material(params):
    reset_scene()
    import_any(str(params["input"]))
    targets = mesh_objects()
    if params.get("part_names"):
        targets = [o for o in targets if o.name in params["part_names"]]
    applied = apply_material(targets, params.get("material") or {})
    out = export_any(str(params["output"]))
    return {"success": True, "path": out, "material": applied}


def op_bake_materials(params):
    """Bake material colors to image textures so node-driven shaders survive
    glTF export. Requires UVs (smart-projected automatically if missing)."""
    reset_scene()
    import bpy
    import bmesh

    if params.get("input"):
        import_any(str(params["input"]))

    size = int(params.get("size", 512))
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = int(params.get("samples", 16))
    scene.cycles.device = "CPU"

    out_dir = params.get("texture_dir")
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    baked = []
    for obj in mesh_objects():
        if not obj.data.uv_layers:
            select_only([obj])
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.smart_project(angle_limit=math.radians(66.0))
            bpy.ops.object.mode_set(mode="OBJECT")

        for slot_index, mat in enumerate(obj.data.materials):
            if not mat or not mat.use_nodes:
                continue
            image = bpy.data.images.new(f"{obj.name}_bake_{slot_index}", width=size, height=size)
            tex_node = mat.node_tree.nodes.new(type="ShaderNodeTexImage")
            tex_node.image = image
            mat.node_tree.nodes.active = tex_node
            select_only([obj])
            bpy.ops.object.bake(
                type="DIFFUSE",
                use_clear=True,
                use_pass_direct=False,
                use_pass_indirect=False,
                use_pass_color=True,
                margin=8,
            )
            # Wire the baked image into Base Color so exporters pick it up.
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf:
                for link in list(mat.node_tree.links):
                    if link.to_socket == bsdf.inputs["Base Color"]:
                        mat.node_tree.links.remove(link)
                mat.node_tree.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
            if out_dir:
                image.filepath_raw = os.path.join(out_dir, f"{obj.name}_{slot_index}.png")
                image.file_format = "PNG"
                image.save()
            baked.append(f"{obj.name}:{mat.name}")

    result = {"success": True, "baked": baked}
    if params.get("output"):
        result["path"] = export_any(str(params["output"]))
    return result


# ── Dispatch ─────────────────────────────────────────────────────────────────


DISPATCH = {
    "info": op_info,
    "build_from_spec": op_build_from_spec,
    "measure": op_measure,
    "count_ngons": op_count_ngons,
    "topology_report": op_topology_report,
    "export_fbx": op_export_fbx,
    "export_usdz": op_export_usdz,
    "render_views": op_render_views,
    "run_script": op_run_script,
    "inspect": op_inspect,
    "import_model": op_import_model,
    "export_model": op_export_model,
    "convert": op_convert,
    "decimate": op_decimate,
    "generate_uvs": op_generate_uvs,
    "scale_to_exact_bounds": op_scale_to_exact_bounds,
    "center_origin": op_center_origin,
    "apply_material": op_apply_material,
    "bake_materials": op_bake_materials,
}


def main():
    try:
        req = read_request()
        op = req.get("op")
        params = req.get("params", {})
        if op not in DISPATCH:
            raise ValueError(f"Unknown operation: '{op}'. Available: {sorted(DISPATCH.keys())}")
        result = DISPATCH[op](params)
        emit(result)
    except Exception as e:  # noqa: BLE001 — the harness must never crash silently
        emit({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(limit=10),
        })


main()
