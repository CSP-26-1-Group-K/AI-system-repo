from __future__ import annotations

from pathlib import Path

import omnigibson as og
import omnigibson.lazy as lazy


BEHAVIOR_ROOT = Path(__file__).resolve().parents[2]
HUMAN_FEMALE_ASSET = BEHAVIOR_ROOT / "asset_pipeline/b1k_pipeline/tools/HumanFemale/HumanFemale.usd"
HUMAN_FEMALE_ASSET_HEIGHT_CM = 147.80453


def _apply_collision(prim):
    pxr = lazy.pxr
    if not prim.HasAPI(pxr.UsdPhysics.CollisionAPI):
        pxr.UsdPhysics.CollisionAPI.Apply(prim)
    if not prim.HasAPI(pxr.PhysxSchema.PhysxCollisionAPI):
        pxr.PhysxSchema.PhysxCollisionAPI.Apply(prim)


def _add_procedural_human(stage, pxr, root_path, height):
    skin = (0.78, 0.58, 0.43)
    shirt = (0.18, 0.32, 0.48)
    pants = (0.08, 0.1, 0.14)
    shoe = (0.02, 0.025, 0.03)
    hair = (0.08, 0.05, 0.035)
    sleeve = (0.12, 0.24, 0.36)

    leg_h = height * 0.40
    torso_h = height * 0.38
    shoulder_z = leg_h + torso_h * 0.72
    torso_radius = height * 0.105
    limb_radius = height * 0.032
    head_radius = height * 0.088

    def capsule(name, rel_pos, radius, capsule_height, color, axis="Z", rx=None, ry=None):
        prim = pxr.UsdGeom.Capsule.Define(stage, f"{root_path}/{name}")
        prim.CreateAxisAttr(axis)
        prim.CreateRadiusAttr(radius)
        prim.CreateHeightAttr(capsule_height)
        prim.AddTranslateOp().Set(pxr.Gf.Vec3d(*rel_pos))
        if rx is not None:
            prim.AddRotateXOp().Set(float(rx))
        if ry is not None:
            prim.AddRotateYOp().Set(float(ry))
        prim.CreateDisplayColorAttr([pxr.Gf.Vec3f(*color)])
        return prim

    def sphere(name, rel_pos, radius, color, scale=(1.0, 1.0, 1.0)):
        prim = pxr.UsdGeom.Sphere.Define(stage, f"{root_path}/{name}")
        prim.CreateRadiusAttr(radius)
        prim.AddTranslateOp().Set(pxr.Gf.Vec3d(*rel_pos))
        if scale != (1.0, 1.0, 1.0):
            prim.AddScaleOp().Set(pxr.Gf.Vec3d(*scale))
        prim.CreateDisplayColorAttr([pxr.Gf.Vec3f(*color)])
        return prim

    capsule("torso", (0.0, 0.0, leg_h + torso_h * 0.48), torso_radius, torso_h, shirt)
    sphere("head", (0.0, 0.0, leg_h + torso_h + head_radius * 1.15), head_radius, skin, scale=(0.92, 0.88, 1.08))
    sphere("hair", (0.0, -head_radius * 0.14, leg_h + torso_h + head_radius * 1.82), head_radius * 0.82, hair, scale=(1.0, 0.86, 0.42))
    capsule("left_leg", (-torso_radius * 0.42, 0.0, leg_h * 0.5), limb_radius, leg_h, pants)
    capsule("right_leg", (torso_radius * 0.42, 0.0, leg_h * 0.5), limb_radius, leg_h, pants)
    capsule("left_arm", (-torso_radius * 1.28, 0.0, shoulder_z), limb_radius, torso_h * 0.68, sleeve, rx=8.0)
    capsule("right_arm", (torso_radius * 1.28, 0.0, shoulder_z), limb_radius, torso_h * 0.68, sleeve, rx=-8.0)
    sphere("left_hand", (-torso_radius * 1.30, 0.04, shoulder_z - torso_h * 0.40), limb_radius * 1.20, skin)
    sphere("right_hand", (torso_radius * 1.30, 0.04, shoulder_z - torso_h * 0.40), limb_radius * 1.20, skin)
    capsule("left_shoe", (-torso_radius * 0.42, 0.08, 0.04), limb_radius * 1.25, 0.16, shoe, axis="Y")
    capsule("right_shoe", (torso_radius * 0.42, 0.08, 0.04), limb_radius * 1.25, 0.16, shoe, axis="Y")
    return "procedural"


def _add_usd_human(stage, pxr, root_path, height):
    asset = HUMAN_FEMALE_ASSET
    if not asset.exists():
        return None

    visual = pxr.UsdGeom.Xform.Define(stage, f"{root_path}/visual")
    visual.GetPrim().GetReferences().AddReference(str(asset))

    scale = height / (HUMAN_FEMALE_ASSET_HEIGHT_CM * 0.01)
    visual.AddScaleOp().Set(pxr.Gf.Vec3d(scale * 0.01, scale * 0.01, scale * 0.01))
    visual.AddTranslateOp().Set(pxr.Gf.Vec3d(0.0, 0.0, 0.135 * scale))
    visual.AddRotateZOp().Set(180.0)

    return asset.name


def add_demo_human_avatar(position, height, collision_enabled=True, show_collision_proxy=False, visual_mode="procedural"):
    stage = og.sim.stage
    pxr = lazy.pxr
    x, y, z = position

    root_path = "/World/dummy_human"
    root = pxr.UsdGeom.Xform.Define(stage, root_path)
    root.AddTranslateOp().Set(pxr.Gf.Vec3d(x, y, z))
    root.AddRotateZOp().Set(0.0)

    visual_asset = None
    if visual_mode == "usd":
        visual_asset = _add_usd_human(stage, pxr, root_path, height)
    if visual_asset is None:
        visual_asset = _add_procedural_human(stage, pxr, root_path, height)

    # Keep the visual humanoid separate from the physics shape so replay validation
    # can disable the proxy without changing the displayed resident.
    collision = pxr.UsdGeom.Capsule.Define(stage, f"{root_path}/collision_proxy")
    collision.CreateAxisAttr("Z")
    collision.CreateRadiusAttr(0.27)
    collision.CreateHeightAttr(height * 0.88)
    collision.AddTranslateOp().Set(pxr.Gf.Vec3d(0.0, 0.0, height * 0.47))
    collision.CreateDisplayColorAttr([pxr.Gf.Vec3f(0.05, 0.9, 0.95)])
    collision.CreateDisplayOpacityAttr([0.22])
    if collision_enabled:
        _apply_collision(collision.GetPrim())
    if not show_collision_proxy:
        pxr.UsdGeom.Imageable(collision.GetPrim()).MakeInvisible()

    return root, visual_asset
