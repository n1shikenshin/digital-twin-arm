import omni.ext
import omni.ui as ui
import omni.usd

from . import arm_model


class MyExtension(omni.ext.IExt):
    def on_startup(self, _ext_id):
        self._handles = None
        self._angles = [0.0] * 6
        self._window = ui.Window("Robot Arm Test", width=380, height=520)
        with self._window.frame:
            with ui.VStack(spacing=6):
                ui.Button("Build / Rebuild Arm", height=32, clicked_fn=self._build_scene)
                self._status = ui.Label("Press the button to build the arm", height=20)
                for i, (name, _axis, _off, (lo, hi)) in enumerate(arm_model.JOINTS):
                    with ui.HStack(height=24):
                        ui.Label(f"{name} ({_axis})", width=60)
                        s = ui.FloatSlider(min=lo, max=hi)
                        s.model.add_value_changed_fn(
                            lambda m, idx=i: self._on_joint(idx, m.as_float))
                with ui.HStack(height=24):
                    ui.Label("Gripper %", width=60)
                    g = ui.FloatSlider(min=0, max=100)
                    g.model.set_value(50.0)
                    g.model.add_value_changed_fn(lambda m: self._on_gripper(m.as_float))
                with ui.HStack(height=24):
                    ui.Label("Motor C", width=60)
                    t = ui.FloatSlider(min=30, max=100)
                    t.model.set_value(30.0)
                    t.model.add_value_changed_fn(lambda m: self._on_temp(m.as_float))

    def on_shutdown(self):
        if self._window:
            self._window.destroy()
            self._window = None

    def _build_scene(self):
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            ctx.new_stage()
            stage = ctx.get_stage()
        self._handles = arm_model.build_robot(stage)
        arm_model.apply_joint_angles(self._handles, self._angles)
        self._status.text = f"Arm built at {arm_model.ROOT_PATH}"

    def _on_joint(self, idx, value):
        self._angles[idx] = value
        if self._handles:
            arm_model.apply_joint_angles(self._handles, self._angles)

    def _on_gripper(self, value):
        if self._handles:
            arm_model.apply_gripper(self._handles, value)

    def _on_temp(self, value):
        if self._handles:
            arm_model.set_motor_temp_color(self._handles, value)