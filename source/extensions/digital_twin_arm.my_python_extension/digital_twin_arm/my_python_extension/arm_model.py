"""Procedural USD model of a 6-DOF robot arm (Z-up, meters)."""
from pxr import Gf, UsdGeom

ROOT_PATH = "/World/Arm_01"

# name, rotation axis, offset from parent frame (m), joint limits (deg)
JOINTS = [
    ("J1", "Z", (0.0, 0.0, 0.10), (-170.0, 170.0)),  # base yaw
    ("J2", "Y", (0.0, 0.0, 0.20), (-120.0, 120.0)),  # shoulder pitch
    ("J3", "Y", (0.0, 0.0, 0.40), (-135.0, 135.0)),  # elbow pitch
    ("J4", "Z", (0.0, 0.0, 0.35), (-180.0, 180.0)),  # forearm roll
    ("J5", "Y", (0.0, 0.0, 0.15), (-120.0, 120.0)),  # wrist pitch
    ("J6", "Z", (0.0, 0.0, 0.10), (-180.0, 180.0)),  # flange roll
]
LINK_RADII = [0.07, 0.055, 0.045, 0.04, 0.035]
COLOR_LINK = (0.85, 0.85, 0.9)
COLOR_JOINT = (0.95, 0.5, 0.1)
COLOR_BASE = (0.25, 0.25, 0.3)


def _cylinder(stage, path, radius, height, center_z, color):
    cyl = UsdGeom.Cylinder.Define(stage, path)
    cyl.CreateRadiusAttr(radius)
    cyl.CreateHeightAttr(height)
    cyl.CreateAxisAttr(UsdGeom.Tokens.z)
    cyl.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    UsdGeom.Xformable(cyl.GetPrim()).AddTranslateOp().Set(Gf.Vec3d(0, 0, center_z))
    return cyl


def _sphere(stage, path, radius, offset, color):
    sph = UsdGeom.Sphere.Define(stage, path)
    sph.CreateRadiusAttr(radius)
    sph.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    UsdGeom.Xformable(sph.GetPrim()).AddTranslateOp().Set(Gf.Vec3d(*offset))
    return sph


def _box(stage, path, size_xyz, center, color):
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    xf = UsdGeom.Xformable(cube.GetPrim())
    t_op = xf.AddTranslateOp()
    t_op.Set(Gf.Vec3d(*center))
    xf.AddScaleOp().Set(Gf.Vec3f(*size_xyz))
    return t_op


def _rot_vec(axis, angle):
    return {"X": Gf.Vec3f(angle, 0, 0),
            "Y": Gf.Vec3f(0, angle, 0),
            "Z": Gf.Vec3f(0, 0, angle)}[axis]


def build_robot(stage, root_path=ROOT_PATH):
    """Create the arm hierarchy and return handles used to animate it."""
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    if stage.GetPrimAtPath(root_path):
        stage.RemovePrim(root_path)
    UsdGeom.Xform.Define(stage, root_path)

    _cylinder(stage, f"{root_path}/Base", 0.15, 0.10, 0.05, COLOR_BASE)

    parent = root_path
    rot_ops, axes = [], []
    for i, (name, axis, offset, _lim) in enumerate(JOINTS):
        jpath = f"{parent}/{name}"
        xf = UsdGeom.Xformable(UsdGeom.Xform.Define(stage, jpath).GetPrim())
        xf.AddTranslateOp().Set(Gf.Vec3d(*offset))
        rot_op = xf.AddRotateXYZOp()
        rot_op.Set(Gf.Vec3f(0, 0, 0))
        rot_ops.append(rot_op)
        axes.append(axis)
        _sphere(stage, f"{jpath}/Marker", 0.06, (0, 0, 0), COLOR_JOINT)
        if i < len(JOINTS) - 1:
            length = JOINTS[i + 1][2][2]
            _cylinder(stage, f"{jpath}/Link{i + 1}", LINK_RADII[i],
                      length, length / 2.0, COLOR_LINK)
        parent = jpath

    # motor housing on J2 (its color will show temperature)
    motor = _sphere(stage, f"{root_path}/J1/J2/Motor", 0.05, (0, 0.09, 0), (0.2, 0.3, 1.0))

    # gripper on J6
    g = f"{parent}/Gripper"
    UsdGeom.Xform.Define(stage, g)
    _box(stage, f"{g}/Palm", (0.06, 0.12, 0.04), (0, 0, 0.03), COLOR_BASE)
    left = _box(stage, f"{g}/FingerL", (0.03, 0.015, 0.10), (0, 0.02, 0.10), COLOR_JOINT)
    right = _box(stage, f"{g}/FingerR", (0.03, 0.015, 0.10), (0, -0.02, 0.10), COLOR_JOINT)

    return {"rot_ops": rot_ops, "axes": axes, "finger_l": left,
            "finger_r": right, "motor": motor}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def apply_joint_angles(handles, angles_deg):
    """angles_deg: list of 6 floats (degrees); clamped to joint limits."""
    for i, op in enumerate(handles["rot_ops"]):
        lo, hi = JOINTS[i][3]
        a = clamp(float(angles_deg[i]), lo, hi)
        op.Set(_rot_vec(handles["axes"][i], a))


def apply_gripper(handles, pct):
    """pct 0 = closed, 100 = open."""
    pct = clamp(float(pct), 0.0, 100.0)
    y = 0.01 + 0.04 * pct / 100.0 + 0.0075
    handles["finger_l"].Set(Gf.Vec3d(0, y, 0.10))
    handles["finger_r"].Set(Gf.Vec3d(0, -y, 0.10))


def set_motor_temp_color(handles, temp_c):
    """Blue at 30 C -> red at 90 C."""
    f = clamp((float(temp_c) - 30.0) / 60.0, 0.0, 1.0)
    handles["motor"].GetDisplayColorAttr().Set([Gf.Vec3f(f, 0.3 * (1 - f), 1 - f)])