import bpy
import json
import threading
import mathutils
from math import radians
import sys
import subprocess
import os
import site
import importlib

# --- ROBUST AUTO-INSTALLER FOR PYSERIAL ---
def install_pyserial():
    try:
        import serial
        return
    except ImportError:
        print("Pyserial not found. Attempting installation...")

    python_exe = sys.executable
    try:
        subprocess.call([python_exe, "-m", "ensurepip", "--user"])
    except Exception as e:
        print(f"Ensurepip failed: {e}")

    commands = [
        [python_exe, "-m", "pip", "install", "pyserial"],
        [python_exe, "-m", "pip", "install", "pyserial", "--user"]
    ]

    success = False
    for cmd in commands:
        try:
            result = subprocess.call(cmd)
            if result == 0:
                success = True
                break
        except Exception:
            pass

    if success:
        print("Pyserial installed! Refreshing paths...")
        importlib.invalidate_caches()
        try:
            user_site = site.getusersitepackages()
            if user_site not in sys.path:
                sys.path.append(user_site)
        except Exception:
            pass
    else:
        print("CRITICAL: Manual install required via terminal.")

install_pyserial()

try:
    import serial
except ImportError:
    raise ImportError("Library installed but not loaded. PLEASE RESTART BLENDER.")

# --- CONFIGURATION ---
SERIAL_PORT = 'COM3'    # <--- CONFIRM THIS IS STILL COM3
BAUD_RATE = 921600      # Matches ESP32

# --- BONE MAPPING (UPDATED) ---
BONE_MAP = {
    0: "UpperArm.008",   # Sensor 0 (0x68) -> Usually Forearm/Wrist
    1: "UpperArm.021"    # Sensor 1 (0x69) -> Usually Upper Arm/Shoulder
}

# --- AXIS CORRECTION ---
# If the arm is twisted, change these offsets
BONE_OFFSETS = {
    0: mathutils.Euler((0, 0, 0), 'XYZ'),
    1: mathutils.Euler((0, 0, 0), 'XYZ')
}

latest_data = {} 
is_running = False
packet_count = 0

def read_serial_thread():
    global is_running, latest_data, packet_count
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.01)
        print(f"SUCCESS: Connected to {SERIAL_PORT}")
        while is_running:
            if ser.in_waiting:
                try:
                    line = ser.readline().decode('utf-8').strip()
                    if line.startswith('{') and line.endswith('}'):
                        data = json.loads(line)
                        if "id" in data and "q" in data:
                            latest_data[data["id"]] = data["q"]
                            packet_count += 1
                            if packet_count % 100 == 0:
                                print(f"DEBUG: Rx Data ID:{data['id']} (Total: {packet_count})")
                except:
                    pass
        ser.close()
    except Exception as e:
        print(f"Serial Error: {e}")
        is_running = False

class RobotMocapOperator(bpy.types.Operator):
    bl_idname = "wm.robot_mocap"
    bl_label = "Start Robot Link"
    _timer = None
    _armature_name = ""

    def modal(self, context, event):
        if event.type == 'ESC':
            return self.cancel(context)

        if event.type == 'TIMER':
            # Use the armature found during execute, or try to find it again
            obj = bpy.data.objects.get(self._armature_name)
            
            if obj:
                for sensor_id, bone_name in BONE_MAP.items():
                    if sensor_id in latest_data:
                        p_bone = obj.pose.bones.get(bone_name)
                        if p_bone:
                            # 1. Get Sensor Rotation
                            w, x, y, z = latest_data[sensor_id]
                            
                            # Standard MPU to Blender mapping
                            # If twisting occurs, try: (w, x, z, -y) or (w, -y, x, z)
                            sensor_quat = mathutils.Quaternion((w, y, x, -z)) 
                            
                            # 2. Apply Offset
                            offset = BONE_OFFSETS.get(sensor_id, mathutils.Euler((0,0,0)))
                            final_rot = sensor_quat @ offset.to_quaternion()
                            
                            # 3. Apply to Bone
                            p_bone.rotation_mode = 'QUATERNION'
                            p_bone.rotation_quaternion = final_rot
                        else:
                            # Debug: Print once if bone missing
                            if packet_count < 5: 
                                print(f"WARNING: Bone '{bone_name}' not found in Armature!")
            else:
                 print(f"CRITICAL: Armature '{self._armature_name}' not found!")
                 return self.cancel(context)
                 
        return {'PASS_THROUGH'}

    def execute(self, context):
        global is_running
        if not is_running:
            # 1. Identify the Armature
            active_obj = context.view_layer.objects.active
            if active_obj and active_obj.type == 'ARMATURE':
                self._armature_name = active_obj.name
                print(f"--------------------------------------------------")
                print(f"TARGET ACQUIRED: Using Armature '{self._armature_name}'")
                print(f"--------------------------------------------------")
            else:
                # Fallback: Look for object named "Armature"
                fallback = bpy.data.objects.get("Armature")
                if fallback:
                    self._armature_name = "Armature"
                    print(f"Using default object 'Armature'")
                else:
                    self.report({'ERROR'}, "Please SELECT your Robot Armature first!")
                    return {'CANCELLED'}

            is_running = True
            t = threading.Thread(target=read_serial_thread, daemon=True)
            t.start()
            wm = context.window_manager
            self._timer = wm.event_timer_add(0.016, window=context.window)
            wm.modal_handler_add(self)
            self.report({'INFO'}, f"Robot Link Started on {SERIAL_PORT}")
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        global is_running
        is_running = False
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        print("Stopped.")
        return {'CANCELLED'}

def register():
    bpy.utils.register_class(RobotMocapOperator)

def unregister():
    bpy.utils.unregister_class(RobotMocapOperator)

if __name__ == "__main__":
    register()
    bpy.ops.wm.robot_mocap()
