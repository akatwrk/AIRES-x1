
import bpy
import json
import threading
import mathutils
from math import degrees, radians
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
SERIAL_PORT = 'COM3'    # CHECK YOUR ESP32 PORT
BAUD_RATE = 921600      

# --- JOINT MAPPING (6 AXIS) ---
# Enter the EXACT name of the bone for each joint
# You must also define which axis that bone rotates around ('X', 'Y', or 'Z')
JOINT_MAP = {
    "Base":     {"bone": "UpperArm.008",     "axis": "Z"}, # Yaw
    "Shoulder": {"bone": "UpperArm.015", "axis": "X"}, # Pitch
    "Elbow":    {"bone": "UpperArm.018",    "axis": "X"}, # Pitch
    "Wrist":    {"bone": "UpperArm.019",    "axis": "Y"}, # Roll
    "Wrist_2":  {"bone": "UpperArm.020",   "axis": "X"}, # New 6th Bone (Placeholder)
    "Gripper":  {"bone": "UpperArm.021",  "axis": "Y"}  # Roll

}

# --- INITIAL / HOME POSE (Angles in Degrees) ---
# When you press ESC, the robot will return to these angles.
INITIAL_POSE = {
    "Base":     0.0,
    "Shoulder": 0.0,
    "Elbow":    0.0,
    "Wrist":    0.0,
    "Wrist_2":  0.0,
    "Gripper":  0.0
}

# --- SENSOR MAPPING ---
# ID 0 = Forearm (IMU2) | ID 1 = Shoulder (IMU1)
IMU_SHOULDER_ID = 1  
IMU_FOREARM_ID = 0   

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
                except:
                    pass
        ser.close()
    except Exception as e:
        print(f"Serial Error: {e}")
        is_running = False

class RobotMocapOperator(bpy.types.Operator):
    bl_idname = "wm.robot_mocap"
    bl_label = "Start Robot Logic"
    _timer = None
    _armature_name = ""

    def modal(self, context, event):
        if event.type == 'ESC':
            return self.cancel(context)

        if event.type == 'TIMER':
            obj = bpy.data.objects.get(self._armature_name)
            
            # Check if we have data from BOTH sensors
            if obj and (IMU_SHOULDER_ID in latest_data) and (IMU_FOREARM_ID in latest_data):
                
                # 1. Get Quaternions
                # IMU 1 (Shoulder)
                q1_raw = latest_data[IMU_SHOULDER_ID]
                q1 = mathutils.Quaternion((q1_raw[0], q1_raw[1], q1_raw[2], q1_raw[3]))
                
                # IMU 2 (Forearm)
                q2_raw = latest_data[IMU_FOREARM_ID]
                q2 = mathutils.Quaternion((q2_raw[0], q2_raw[1], q2_raw[2], q2_raw[3]))

                # 2. Convert to Euler Angles (Radians)
                # 'ZYX' usually maps to: Z=Yaw, Y=Pitch, X=Roll (depending on mounting)
                # If your sensor is mounted sideways, you might need to swap these.
                imu1_euler = q1.to_euler('ZYX')
                imu2_euler = q2.to_euler('ZYX')

                # Extract Logic Variables
                # Assumes: Z=Yaw, Y=Pitch, X=Roll. 
                # Adjust these variables if your axes are different!
                imu1_yaw   = imu1_euler.z
                imu1_pitch = imu1_euler.y
                imu1_roll  = imu1_euler.x

                imu2_pitch = imu2_euler.y
                imu2_roll  = imu2_euler.x

                # 3. Calculate Target Angles (The 6-Axis Logic)
                target_angles = {}
                
                # Logic Table Implementation:
                target_angles["Base"]     = imu1_yaw                        # IMU1 Yaw
                target_angles["Shoulder"] = imu1_pitch                      # IMU1 Pitch
                target_angles["Elbow"]    = imu2_pitch - imu1_pitch         # IMU2 - IMU1 Pitch
                target_angles["Wrist"]    = imu2_roll - imu1_roll           # IMU2 - IMU1 Roll
                target_angles["Wrist_2"]  = 0.0                             # New 6th Bone (Set logic here)
                target_angles["Gripper"]  = imu2_roll                       # IMU2 Roll

                # 4. Apply to Bones
                for joint_name, config in JOINT_MAP.items():
                    bone_name = config["bone"]
                    axis = config["axis"]
                    
                    p_bone = obj.pose.bones.get(bone_name)
                    if p_bone:
                        # Set Rotation Mode to Euler for single-axis control
                        p_bone.rotation_mode = 'XYZ'
                        
                        angle = target_angles[joint_name]

                        # Apply angle to the specific axis
                        if axis == 'X':
                            p_bone.rotation_euler.x = angle
                        elif axis == 'Y':
                            p_bone.rotation_euler.y = angle
                        elif axis == 'Z':
                            p_bone.rotation_euler.z = angle
            
        return {'PASS_THROUGH'}

    def execute(self, context):
        global is_running
        if not is_running:
            # Find Armature
            active_obj = context.view_layer.objects.active
            if active_obj and active_obj.type == 'ARMATURE':
                self._armature_name = active_obj.name
            else:
                fallback = bpy.data.objects.get("Armature")
                if fallback: self._armature_name = "Armature"
                else:
                    self.report({'ERROR'}, "Select Armature first!")
                    return {'CANCELLED'}

            is_running = True
            t = threading.Thread(target=read_serial_thread, daemon=True)
            t.start()
            wm = context.window_manager
            self._timer = wm.event_timer_add(0.016, window=context.window)
            wm.modal_handler_add(self)
            print("6-Axis Logic Started.")
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        global is_running
        is_running = False
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        
        # --- RESET TO INITIAL POSE ---
        print("Resetting Robot to Home Position...")
        obj = bpy.data.objects.get(self._armature_name)
        if obj:
            for joint_name, config in JOINT_MAP.items():
                bone_name = config["bone"]
                axis = config["axis"]
                
                # Get initial angle in degrees and convert to radians
                target_deg = INITIAL_POSE.get(joint_name, 0.0)
                target_rad = radians(target_deg)
                
                p_bone = obj.pose.bones.get(bone_name)
                if p_bone:
                    p_bone.rotation_mode = 'XYZ'
                    # Apply initial angle to the correct axis
                    if axis == 'X':
                        p_bone.rotation_euler.x = target_rad
                    elif axis == 'Y':
                        p_bone.rotation_euler.y = target_rad
                    elif axis == 'Z':
                        p_bone.rotation_euler.z = target_rad

        print("Stopped.")
        return {'CANCELLED'}

def register():
    bpy.utils.register_class(RobotMocapOperator)

def unregister():
    bpy.utils.unregister_class(RobotMocapOperator)

if __name__ == "__main__":
    register()
    bpy.ops.wm.robot_mocap()
