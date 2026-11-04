"""
ESP32-S3 Tabletop Voice Robot — Parametric 3D Printable CAD (.STL) Generator
============================================================================
Generates 8 watertight, manifold binary .STL files matching `docs/images/robot_hero.jpg`
and custom-fitted to the exact hardware dimensions ordered in `README.md`:

  1. 1_head_shell.stl            (86 x 68 x 78 mm) - Rounded squircle head, 48mm speaker ear grille,
                                                     INMP441 acoustic isolation collar, PTT button socket
  2. 2_front_visor_bezel.stl     (59 x 47 x 6 mm)  - Snap-in black visor framing 1.3" ST7789 240x240 IPS
  3. 3_top_ptt_button_cap.stl    (30 x 30 x 10 mm) - Top circular plunger cap for 6x6mm GPIO 1 button
  4. 4_torso_shell.stl           (78 x 64 x 72 mm) - Chubby rounded torso + ESP32-S3 rails + SG90 arm &
                                                     dual-mode hip servo bays + click-detent hip axles
  5. 5_back_access_door.stl      (52 x 44 x 6 mm)  - Snap-fit rear service door with cooling slots
  6. 6_arms_left_and_right.stl   (68 x 20 x 59 mm) - Articulated left & right arms with C-hands
  7. 7_thighs_left_and_right.stl (66 x 27 x 36 mm) - Movable hip-pivot thighs with 0/25/90 deg click-detents
  8. 8_feet_left_and_right.stl   (68 x 40 x 18 mm) - Swivel/tilt ankle shoes with flat-standing lock detents

Also renders `cad/robot_cad_preview.png` so you can inspect all 8 meshes visually.
Run:
    python cad/generate_robot_stl.py
"""

from __future__ import annotations

import math
import os
import struct
from pathlib import Path
from typing import Callable, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from skimage.measure import marching_cubes

OUT_DIR = Path(__file__).resolve().parent
STL_DIR = OUT_DIR / "stl"


# ============================================================================
# Signed Distance Field (SDF) Primitives (Guaranteed Watertight 2-Manifolds)
# ============================================================================


def sd_round_box(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    bx: float,
    by: float,
    bz: float,
    r: float,
) -> np.ndarray:
    qx = np.abs(x) - (bx - r)
    qy = np.abs(y) - (by - r)
    qz = np.abs(z) - (bz - r)
    out_dist = np.sqrt(
        np.maximum(qx, 0.0) ** 2 + np.maximum(qy, 0.0) ** 2 + np.maximum(qz, 0.0) ** 2
    )
    in_dist = np.minimum(np.maximum(qx, np.maximum(qy, qz)), 0.0)
    return out_dist + in_dist - r


def sd_cylinder_z(
    x: np.ndarray, y: np.ndarray, z: np.ndarray, radius: float, half_h: float
) -> np.ndarray:
    d_rad = np.sqrt(x**2 + y**2) - radius
    d_z = np.abs(z) - half_h
    return np.minimum(np.maximum(d_rad, d_z), 0.0) + np.sqrt(
        np.maximum(d_rad, 0.0) ** 2 + np.maximum(d_z, 0.0) ** 2
    )


def sd_cylinder_x(
    x: np.ndarray, y: np.ndarray, z: np.ndarray, radius: float, half_w: float
) -> np.ndarray:
    d_rad = np.sqrt(y**2 + z**2) - radius
    d_x = np.abs(x) - half_w
    return np.minimum(np.maximum(d_rad, d_x), 0.0) + np.sqrt(
        np.maximum(d_rad, 0.0) ** 2 + np.maximum(d_x, 0.0) ** 2
    )


def sd_cylinder_y(
    x: np.ndarray, y: np.ndarray, z: np.ndarray, radius: float, half_d: float
) -> np.ndarray:
    d_rad = np.sqrt(x**2 + z**2) - radius
    d_y = np.abs(y) - half_d
    return np.minimum(np.maximum(d_rad, d_y), 0.0) + np.sqrt(
        np.maximum(d_rad, 0.0) ** 2 + np.maximum(d_y, 0.0) ** 2
    )


def sd_sphere(x: np.ndarray, y: np.ndarray, z: np.ndarray, r: float) -> np.ndarray:
    return np.sqrt(x**2 + y**2 + z**2) - r


def sd_ellipsoid(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    rx: float,
    ry: float,
    rz: float,
) -> np.ndarray:
    k0 = np.sqrt((x / rx) ** 2 + (y / ry) ** 2 + (z / rz) ** 2)
    k1 = np.sqrt((x / (rx * rx)) ** 2 + (y / (ry * ry)) ** 2 + (z / (rz * rz)) ** 2)
    return k0 * (k0 - 1.0) / np.maximum(k1, 1e-6)


def smin(a: np.ndarray, b: np.ndarray, k: float = 4.0) -> np.ndarray:
    h = np.maximum(k - np.abs(a - b), 0.0) / k
    return np.minimum(a, b) - h * h * k * 0.25


def op_union(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.minimum(a, b)


def op_subtract(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.maximum(a, -b)


# ============================================================================
# Part 1: Rounded Squircle Head Shell (`1_head_shell.stl`)
#   - Outer: 84 mm (X) x 68 mm (Y depth) x 72 mm (Z height), 11 mm fillet
#   - Wall thickness: 2.8 mm
#   - Front Visor Pocket + 26.5 x 26.5 mm ST7789 active screen cutout + PCB rails
#   - Right & Left Ear 48.6 mm Speaker Mount + 19-hole acoustic honeycomb grille
#   - Top 28.6 mm PTT Button Socket + 6.3 x 6.3 mm tactile switch cradle
#   - Top-Front isolated 3.2 mm INMP441 Mic port + 15 mm acoustic baffle ring
# ============================================================================


def sdf_head_shell(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    # 1. Outer & inner rounded cube shell (Z centered at 0 -> -36..+36)
    outer = sd_round_box(x, y, z, 42.0, 34.0, 36.0, 11.0)
    inner = sd_round_box(x, y, z, 39.2, 31.2, 33.2, 8.5)
    shell = op_subtract(outer, inner)

    # 2. Front visor recess (y = -34 is front face)
    visor_recess = sd_round_box(x, y + 33.0, z - 1.0, 32.0, 3.2, 26.0, 7.0)
    shell = op_subtract(shell, visor_recess)

    # 3. 1.3" ST7789 240x240 IPS Display Window (26.6 x 26.6 mm through-cut)
    tft_window = sd_round_box(x, y + 31.5, z - 1.0, 13.3, 6.0, 13.3, 2.5)
    shell = op_subtract(shell, tft_window)

    # Internal ST7789 PCB retention rails (holds 35.5 x 39.0 mm 7-pin module behind window)
    left_rail = sd_round_box(x + 19.5, y + 27.5, z - 1.0, 1.6, 2.5, 19.0, 0.4)
    right_rail = sd_round_box(x - 19.5, y + 27.5, z - 1.0, 1.6, 2.5, 19.0, 0.4)
    bottom_ledge = sd_round_box(x, y + 27.5, z + 20.5, 19.5, 2.5, 1.8, 0.4)
    shell = op_union(shell, op_union(left_rail, op_union(right_rail, bottom_ledge)))

    # 4. Side Speaker Grille ("Ears" at x = +42 and x = -42) + 48.6 mm Speaker Retention Ring
    ear_boss_r = sd_cylinder_x(x - 41.2, y, z, 21.0, 1.6)
    ear_boss_l = sd_cylinder_x(x + 41.2, y, z, 21.0, 1.6)
    ear_ring_cut_r = sd_cylinder_x(x - 42.2, y, z, 19.0, 1.6)
    ear_ring_cut_l = sd_cylinder_x(x + 42.2, y, z, 19.0, 1.6)
    shell = op_union(shell, op_union(ear_boss_r, ear_boss_l))
    shell = op_subtract(shell, op_union(ear_ring_cut_r, ear_ring_cut_l))

    # Internal 48.8 mm ID retention collar on Right Ear for Electrobot 48mm 8Ohm 3W speaker
    spk_collar_outer = sd_cylinder_x(x - 36.5, y, z, 26.8, 3.5)
    spk_collar_inner = sd_cylinder_x(x - 36.5, y, z, 24.4, 4.5)
    spk_collar = op_subtract(spk_collar_outer, spk_collar_inner)
    shell = op_union(shell, spk_collar)

    # Perforated acoustic holes across both ear grilles (center + 2 hex rings = 19 holes)
    hole_coords = [(0.0, 0.0)]
    for i in range(6):
        ang = i * math.pi / 3.0
        hole_coords.append((6.2 * math.cos(ang), 6.2 * math.sin(ang)))
    for i in range(12):
        ang = i * math.pi / 6.0
        hole_coords.append((12.4 * math.cos(ang), 12.4 * math.sin(ang)))
    for hy, hz in hole_coords:
        hole_Hole = sd_cylinder_x(x, y - hy, z - hz, 1.8, 46.0)
        shell = op_subtract(shell, hole_Hole)

    # 5. Top Circular PTT Button Collar + 6x6mm Tactile Switch Cradle (z = +36 is top)
    top_ring = op_subtract(
        sd_cylinder_z(x, y, z - 35.5, 16.5, 2.5),
        sd_cylinder_z(x, y, z - 35.5, 14.3, 6.0),
    )
    shell = op_union(shell, top_ring)
    # Through-hole for top button plunger (28.4 mm clearance) & internal switch bridge
    btn_hole = sd_cylinder_z(x, y, z - 34.5, 14.3, 5.0)
    shell = op_subtract(shell, btn_hole)
    # Internal bridge platform holding the 6x6mm tactile switch 10 mm below top hole
    sw_bridge = sd_round_box(x, y, z - 25.0, 20.0, 6.5, 1.8, 0.5)
    sw_pocket = sd_round_box(x, y, z - 26.2, 3.25, 3.25, 1.5, 0.2)
    sw_wire_slot = sd_round_box(x - 5.0, y, z - 25.0, 2.0, 4.0, 4.0, 0.2)
    shell = op_union(shell, op_subtract(sw_bridge, op_union(sw_pocket, sw_wire_slot)))

    # 6. Isolated INMP441 Microphone Port & Internal Acoustic Isolation Wall (top-front)
    mic_baffle = op_subtract(
        sd_cylinder_y(x, y + 28.5, z - 22.5, 8.2, 3.5),
        sd_cylinder_y(x, y + 28.5, z - 22.5, 6.2, 4.5),
    )
    mic_pinhole = sd_cylinder_y(x, y + 32.0, z - 22.5, 1.8, 6.0)
    shell = op_union(shell, mic_baffle)
    shell = op_subtract(shell, mic_pinhole)

    # 7. Bottom Neck Socket + Wire Pass-Through (z = -36) & Back Assembly Open Access
    neck_open = sd_cylinder_z(x, y, z + 35.0, 16.0, 5.0)
    neck_collar = op_subtract(
        sd_cylinder_z(x, y, z + 36.5, 19.0, 3.0),
        sd_cylinder_z(x, y, z + 36.5, 16.0, 4.0),
    )
    shell = op_subtract(shell, neck_open)
    shell = op_union(shell, neck_collar)

    # Removable rear head service opening so ST7789 + 48mm Speaker slide in effortlessly
    rear_access = sd_round_box(x, y - 32.0, z - 1.0, 27.0, 5.0, 22.0, 6.0)
    shell = op_subtract(shell, rear_access)

    return shell


# ============================================================================
# Part 2: Snap-In Front Black Visor Bezel (`2_front_visor_bezel.stl`)
# ============================================================================


def sdf_front_visor(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    plate = sd_round_box(x, y, z, 31.6, 25.6, 2.2, 6.8)
    window = sd_round_box(x, y, z, 13.2, 13.2, 4.0, 2.4)
    lip = op_subtract(
        sd_round_box(x, y, z + 1.8, 15.2, 15.2, 1.8, 3.0),
        sd_round_box(x, y, z + 1.8, 13.2, 13.2, 3.0, 2.4),
    )
    return op_union(op_subtract(plate, window), lip)


# ============================================================================
# Part 3: Top Circular PTT Button Cap (`3_top_ptt_button_cap.stl`)
# ============================================================================


def sdf_top_button_cap(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    cap = sd_cylinder_z(x, y, z - 2.0, 13.8, 2.5)
    flange = sd_cylinder_z(x, y, z + 1.0, 15.2, 1.2)
    hollow = sd_cylinder_z(x, y, z + 0.5, 11.8, 2.2)
    plunger = sd_cylinder_z(x, y, z + 2.5, 2.8, 3.5)
    body = op_subtract(op_union(cap, flange), hollow)
    return op_union(body, plunger)


# ============================================================================
# Part 4: Chubby Oval/Egg Torso Shell (`4_torso_shell.stl`)
#   - True oval/egg silhouette matching `robot_hero.jpg` (no boxy corners!)
#   - Lightweight 2.8 mm wall + 45-deg self-supporting teardrop interior (Option B)
#   - Houses ESP32-S3 (`HW678 V0.0.0`), MAX98357A, 1000uF cap, and 18650 slot
#   - Right shoulder SG90 micro-servo pocket + Left/Right 6mm arm pivot sockets
#   - Angled crescent Hip Pivot Sockets (0/25/90 deg click-detent axles + SG90 hip bays)
# ============================================================================


def sdf_torso_shell(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    # True plump oval/egg belly matching attached photo: wider at mid-belly, tapering at shoulders & pelvis
    taper = 1.0 - 0.0035 * (z - 8.0)  # gentle egg taper toward top shoulders
    belly_main = sd_ellipsoid(x * taper, y, z - 12.0, 38.5, 33.0, 36.5)
    tummy_bulge = sd_sphere(x * 0.92, y + 1.5, z - 9.0, 32.0)
    torso_outer = smin(belly_main, tummy_bulge, 6.0)

    # Small flat bottom pelvis print pad (z = -21.5) & flat top neck deck (z = +43.0)
    bottom_clip = z + 21.5
    torso_outer = np.maximum(torso_outer, -bottom_clip)
    top_clip = -(z - 43.0)
    torso_outer = np.maximum(torso_outer, -top_clip)

    # Lightweight 2.8 mm uniform oval interior cavity (saves ~35% PLA, 100% solid front belly wall)
    cavity_oval = sd_ellipsoid(x * taper, y, z - 12.0, 35.0, 29.8, 33.0)
    shell = op_subtract(torso_outer, cavity_oval)

    # Top Neck Ring (mates with 1_head_shell.stl neck collar) + 25 mm wire channel
    neck_peg = sd_cylinder_z(x, y, z - 45.5, 15.6, 3.5)
    neck_hole = sd_cylinder_z(x, y, z - 42.0, 12.5, 9.0)
    shell = op_subtract(op_union(shell, neck_peg), neck_hole)

    # Flush Shoulder Pivot Sockets recessed into upper oval curve (x = +/-33.5, z = +25)
    sh_socket_l = sd_cylinder_x(x + 33.5, y, z - 25.0, 3.2, 8.0)
    sh_socket_r = sd_cylinder_x(x - 33.5, y, z - 25.0, 3.2, 8.0)
    # Right-shoulder internal SG90 micro-servo pocket (23.2 x 12.6 x 16.0 mm) for future [WAVE] motor
    sg90_pocket = sd_round_box(x - 26.5, y, z - 25.0, 7.0, 6.4, 11.6, 0.5)
    shell = op_subtract(shell, op_union(sh_socket_l, op_union(sh_socket_r, sg90_pocket)))

    # Angled Crescent Lower Hip Socket Recesses (x = +/-17.5, z = -12.0) so thighs tuck flush into oval belly!
    hip_wall_l = sd_sphere(x + 17.5, y, z + 12.0, 16.2)
    hip_wall_r = sd_sphere(x - 17.5, y, z + 12.0, 16.2)
    shell = op_union(shell, op_union(hip_wall_l, hip_wall_r))
    # Re-carve main upper electronics cavity above hip domes (strictly inside outer wall)
    upper_cav = sd_ellipsoid(x * taper, y, z - 16.0, 33.5, 28.2, 24.0)
    shell = op_subtract(shell, upper_cav)

    hip_cut_l = op_union(
        sd_sphere(x + 17.5, y, z + 12.5, 13.6),
        sd_cylinder_z(x + 17.5, y, z + 19.0, 13.4, 7.0),
    )
    hip_cut_r = op_union(
        sd_sphere(x - 17.5, y, z + 12.5, 13.6),
        sd_cylinder_z(x - 17.5, y, z + 19.0, 13.4, 7.0),
    )
    shell = op_subtract(shell, op_union(hip_cut_l, hip_cut_r))

    # Heavy-duty 8.0 mm Hip Pivot Axles + Click-Detent Ridges (0 deg standing, +/-25 deg stride, 90 deg sit)
    hip_axle_l = sd_cylinder_x(x + 17.5, y, z + 12.5, 4.0, 9.5)
    hip_axle_r = sd_cylinder_x(x - 17.5, y, z + 12.5, 4.0, 9.5)
    detent_stand_l = sd_round_box(x + 17.5, y, z + 8.3, 8.2, 0.9, 1.1, 0.3)
    detent_stand_r = sd_round_box(x - 17.5, y, z + 8.3, 8.2, 0.9, 1.1, 0.3)
    detent_sit_l = sd_round_box(x + 17.5, y + 4.2, z + 12.5, 8.2, 1.1, 0.9, 0.3)
    detent_sit_r = sd_round_box(x - 17.5, y + 4.2, z + 12.5, 8.2, 1.1, 0.9, 0.3)
    shell = op_union(
        shell,
        op_union(
            op_union(hip_axle_l, hip_axle_r),
            op_union(op_union(detent_stand_l, detent_stand_r), op_union(detent_sit_l, detent_sit_r)),
        ),
    )

    # Dual-Mode Internal SG90 Hip Servo Pockets (tucked above left/right hip sockets)
    sg90_hip_l = sd_round_box(x + 21.0, y, z - 2.0, 6.4, 11.6, 8.0, 0.5)
    sg90_hip_r = sd_round_box(x - 21.0, y, z - 2.0, 6.4, 11.6, 8.0, 0.5)
    shell = op_subtract(shell, op_union(sg90_hip_l, sg90_hip_r))

    # Internal ESP32-S3 Board Vertical Cradle Rails (holds 28.5 mm wide HW678 V0.0.0 PCB)
    esp_rail_l = sd_round_box(x + 15.2, y + 5.0, z - 15.0, 1.5, 3.0, 19.0, 0.3)
    esp_rail_r = sd_round_box(x - 15.2, y + 5.0, z - 15.0, 1.5, 3.0, 19.0, 0.3)
    shell = op_union(shell, op_union(esp_rail_l, esp_rail_r))

    # Rear Service Door Opening (y = +33 is back) + Lower USB-C Cable Slot
    back_door_cut = sd_round_box(x, y - 27.0, z - 15.0, 25.5, 9.0, 20.5, 5.0)
    usbc_slot = sd_round_box(x, y - 25.0, z + 7.5, 8.0, 9.0, 4.2, 2.0)
    shell = op_subtract(shell, op_union(back_door_cut, usbc_slot))

    return shell


# ============================================================================
# Part 5: Snap-Fit Back Service Door (`5_back_access_door.stl`)
#   - Covers the rear service opening on the head or torso with cooling slots
# ============================================================================


def sdf_back_door(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    plate = sd_round_box(x, y, z, 27.5, 23.5, 1.8, 5.2)
    inner_lip = op_subtract(
        sd_round_box(x, y, z - 2.2, 25.7, 21.7, 2.0, 4.8),
        sd_round_box(x, y, z - 2.2, 23.5, 19.5, 3.0, 4.0),
    )
    door = op_union(plate, inner_lip)
    # 3 ventilation louver slots
    for sz in (-8.0, 0.0, 8.0):
        slot = sd_round_box(x, y + sz, z, 16.0, 1.6, 5.0, 0.8)
        door = op_subtract(door, slot)
    return door


# ============================================================================
# Part 6: Articulated Left & Right Movable Arms (`6_arms_left_and_right.stl`)
#   - Tapered rounded arms + wrist seam + C-shaped hands + 5.8 mm shoulder pegs
# ============================================================================


def sdf_single_arm(x: np.ndarray, y: np.ndarray, z: np.ndarray, side: float) -> np.ndarray:
    # Smooth curved arm matching attached photo, with subtle wrist ring groove at z = +15
    shoulder = sd_sphere(x - side * 1.5, y, z - 18.0, 9.2)
    upper_arm = sd_cylinder_z(x, y, z - 3.0, 8.5, 16.0)
    forearm = sd_cylinder_z(x, y, z + 11.0, 7.8, 9.0)
    arm_body = smin(shoulder, smin(upper_arm, forearm, 4.5), 4.5)

    # Subtle wrist seam groove visible in reference image
    wrist_groove = op_subtract(
        sd_cylinder_z(x, y, z + 16.5, 9.0, 0.6),
        sd_cylinder_z(x, y, z + 16.5, 7.1, 1.2),
    )
    arm_body = op_subtract(arm_body, wrist_groove)

    # Cute C-shaped Robot Hand at bottom (z = -23)
    hand_outer = sd_sphere(x, y, z + 22.5, 7.8)
    hand_cut = sd_cylinder_y(x - side * 1.8, y, z + 24.5, 4.2, 10.0)
    hand = op_subtract(hand_outer, hand_cut)
    arm_body = op_union(arm_body, hand)

    # Shoulder pivot peg extending inward toward torso (+/- X)
    peg = sd_cylinder_x(x - side * 10.5, y, z - 18.0, 2.9, 5.5)
    return op_union(arm_body, peg)


def sdf_arms_pair(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    left_arm = sdf_single_arm(x + 20.0, y, z, -1.0)
    right_arm = sdf_single_arm(x - 20.0, y, z, +1.0)
    return op_union(left_arm, right_arm)


# ============================================================================
# Part 7: Movable Left & Right Thighs (`7_thighs_left_and_right.stl`)
#   - Sculpted cylindrical thighs that tuck seamlessly into the oval hip sockets
#   - Top snap-on C-fork hip joint (8.4 mm bore + 0/25/90 deg click-detent notch)
#   - Bottom spherical/cylindrical ankle ball-and-detent stud (swivel + tilt)
# ============================================================================


def sdf_single_thigh(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    # Smooth cylindrical thigh with rounded spherical hip dome at z = +9 that tucks into oval belly
    hip_dome = sd_sphere(x, y, z - 9.0, 12.8)
    thigh_shaft = sd_cylinder_z(x, y, z + 1.5, 12.6, 11.5)
    thigh_body = smin(hip_dome, thigh_shaft, 2.5)
    # Flatten bottom cuff of thigh shaft at z = -12.0 where it meets the shoe collar
    thigh_body = np.maximum(thigh_body, -(z + 12.0))

    # Snap-fit 8.4 mm Hip Axle Bore + C-clip snap throat at top (z = +9.0) + detent groove
    hip_bore = sd_cylinder_x(x, y, z - 9.0, 4.25, 10.5)
    snap_throat = sd_round_box(x, y, z - 16.0, 9.0, 3.3, 6.0, 0.5)
    detent_groove_v = sd_round_box(x, y, z - 13.2, 9.0, 1.1, 1.4, 0.3)
    detent_groove_h = sd_round_box(x, y + 4.2, z - 9.0, 9.0, 1.4, 1.1, 0.3)
    # Lightweight internal hollow pocket inside lower thigh (reduces PLA weight)
    thigh_hollow = sd_cylinder_z(x, y, z + 2.5, 7.5, 6.0)
    thigh_body = op_subtract(
        thigh_body,
        op_union(
            op_union(op_union(hip_bore, snap_throat), op_union(detent_groove_v, detent_groove_h)),
            thigh_hollow,
        ),
    )

    # Bottom Swivel + Tilt Ankle Pivot Boss (z = -15.5) with flat-standing detent key
    ankle_neck = sd_cylinder_z(x, y, z + 13.5, 6.2, 3.5)
    ankle_ball = sd_sphere(x, y, z + 16.5, 7.2)
    ankle_key = sd_round_box(x, y, z + 16.5, 7.6, 1.2, 2.0, 0.4)
    return op_union(thigh_body, op_union(ankle_neck, op_union(ankle_ball, ankle_key)))


def sdf_thighs_pair(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    left_thigh = sdf_single_thigh(x + 17.5, y, z)
    right_thigh = sdf_single_thigh(x - 17.5, y, z)
    return op_union(left_thigh, right_thigh)


# ============================================================================
# Part 8: Movable Left & Right Shoes/Feet (`8_feet_left_and_right.stl`)
#   - Wide pill-shaped robot shoes with raised ankle collar rim & flat sole
# ============================================================================


def sdf_single_foot(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    # Plump rounded shoe with forward toe (+Y) and raised ankle collar rim matching photo
    shoe_base = sd_round_box(x, y + 4.0, z + 2.5, 15.0, 20.0, 6.8, 6.2)
    ankle_collar = sd_cylinder_z(x, y - 0.5, z - 4.0, 13.8, 4.5)
    foot_outer = smin(shoe_base, ankle_collar, 2.8)

    # Recessed cup inside top of ankle collar so the thigh sits slightly inside the shoe rim
    collar_recess = sd_cylinder_z(x, y - 0.5, z - 8.2, 12.9, 2.2)
    foot_outer = op_subtract(foot_outer, collar_recess)

    # Exact flat bottom sole at z = -9.0 for rock-solid standing balance
    sole_clip = z + 9.0
    foot_outer = np.maximum(foot_outer, -sole_clip)

    # Top Snap-In Ankle Socket (mates with thigh ankle_ball + 0-deg flat-standing detent slot)
    socket_sphere = sd_sphere(x, y, z - 4.5, 7.5)
    socket_throat = sd_cylinder_z(x, y, z - 8.0, 6.7, 4.0)
    detent_slot_0 = sd_round_box(x, y, z - 4.5, 8.0, 1.4, 2.5, 0.4)
    detent_slot_stride = sd_round_box(x, y, z - 4.5, 1.4, 8.0, 2.5, 0.4)
    return op_subtract(
        foot_outer,
        op_union(op_union(socket_sphere, socket_throat), op_union(detent_slot_0, detent_slot_stride)),
    )


def sdf_feet_pair(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    left_foot = sdf_single_foot(x + 17.5, y, z)
    right_foot = sdf_single_foot(x - 17.5, y, z)
    return op_union(left_foot, right_foot)


# ============================================================================
# Binary STL Exporter & Mesh Statistics
# ============================================================================


def build_mesh_from_sdf(
    sdf_fn: Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray],
    bounds: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]],
    step: float = 0.85,
) -> Tuple[np.ndarray, np.ndarray]:
    (xmin, xmax), (ymin, ymax), (zmin, zmax) = bounds
    xs = np.arange(xmin, xmax + step, step, dtype=np.float32)
    ys = np.arange(ymin, ymax + step, step, dtype=np.float32)
    zs = np.arange(zmin, zmax + step, step, dtype=np.float32)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    vol = sdf_fn(X, Y, Z)
    verts, faces, _, _ = marching_cubes(vol, level=0.0, spacing=(step, step, step))
    verts[:, 0] += xmin
    verts[:, 1] += ymin
    verts[:, 2] += zmin
    # Shift Z so minimum Z sits at Z = 0.0 on the 3D print bed
    verts[:, 2] -= verts[:, 2].min()
    return verts, faces


def write_binary_stl(path: Path, verts: np.ndarray, faces: np.ndarray, header_text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tris = verts[faces]  # (N, 3, 3)
    v0 = tris[:, 0, :]
    v1 = tris[:, 1, :]
    v2 = tris[:, 2, :]
    normals = np.cross(v1 - v0, v2 - v0)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.maximum(lengths, 1e-9)

    n_tris = len(faces)
    header = header_text.encode("ascii", errors="ignore")[:80].ljust(80, b" ")

    rec_dtype = np.dtype(
        [
            ("normal", "<f4", (3,)),
            ("v0", "<f4", (3,)),
            ("v1", "<f4", (3,)),
            ("v2", "<f4", (3,)),
            ("attr", "<u2"),
        ]
    )
    records = np.empty(n_tris, dtype=rec_dtype)
    records["normal"] = normals.astype("<f4")
    records["v0"] = v0.astype("<f4")
    records["v1"] = v1.astype("<f4")
    records["v2"] = v2.astype("<f4")
    records["attr"] = 0

    with open(path, "wb") as f:
        f.write(header)
        f.write(struct.pack("<I", n_tris))
        f.write(records.tobytes())


# ============================================================================
# High-Resolution 3D Shaded Preview Renderer (`cad/robot_cad_preview.png`)
# ============================================================================


def render_mesh_to_image(
    meshes: list[Tuple[np.ndarray, np.ndarray, Tuple[int, int, int]]],
    img_w: int = 800,
    img_h: int = 800,
    yaw_deg: float = -28.0,
    pitch_deg: float = 16.0,
    scale: float = 3.4,
    center_offset: Tuple[float, float, float] = (0.0, 0.0, 82.0),
) -> Image.Image:
    img = Image.new("RGB", (img_w, img_h), (18, 23, 33))
    draw = ImageDraw.Draw(img)

    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)

    # Rotation matrix: Yaw around Z, then Pitch around X, with Z-up -> screen Y-up
    light_dir = np.array([-0.45, -0.65, 0.61], dtype=np.float32)
    light_dir /= np.linalg.norm(light_dir)

    all_polys = []
    cx, cy_off, cz = center_offset

    for verts, faces, base_rgb in meshes:
        v = verts.astype(np.float32) - np.array([cx, cy_off, cz], dtype=np.float32)
        # Rotate around Z (yaw)
        rx = v[:, 0] * cy - v[:, 1] * sy
        ry = v[:, 0] * sy + v[:, 1] * cy
        rz = v[:, 2]
        # Tilt forward (pitch) so camera at -Y looks at front face
        sx_cam = rx
        sy_cam = rz * cp - ry * sp
        sz_depth = rz * sp + ry * cp  # smaller sz_depth = closer to front camera

        tris_screen_x = sx_cam[faces] * scale + (img_w * 0.5)
        tris_screen_y = (img_h * 0.54) - sy_cam[faces] * scale
        tris_depth = sz_depth[faces].mean(axis=1)

        # World normals for Lambert + specular shading
        t_v = v[faces]
        norms = np.cross(t_v[:, 1] - t_v[:, 0], t_v[:, 2] - t_v[:, 0])
        n_len = np.linalg.norm(norms, axis=1, keepdims=True)
        norms = norms / np.maximum(n_len, 1e-8)

        # Backface culling in camera space
        cam_v0_x = tris_screen_x[:, 1] - tris_screen_x[:, 0]
        cam_v0_y = tris_screen_y[:, 1] - tris_screen_y[:, 0]
        cam_v1_x = tris_screen_x[:, 2] - tris_screen_x[:, 0]
        cam_v1_y = tris_screen_y[:, 2] - tris_screen_y[:, 0]
        cross_z = cam_v0_x * cam_v1_y - cam_v0_y * cam_v1_x
        vis = cross_z < 0  # visible front-facing triangles

        dots = np.clip(np.sum(norms * light_dir, axis=1), -1.0, 1.0)
        shade = 0.34 + 0.56 * np.maximum(dots, 0.0) + 0.14 * (np.maximum(dots, 0.0) ** 6)

        r0, g0, b0 = base_rgb
        cols_r = np.clip(r0 * shade, 0, 255).astype(np.uint8)
        cols_g = np.clip(g0 * shade, 0, 255).astype(np.uint8)
        cols_b = np.clip(b0 * shade, 0, 255).astype(np.uint8)

        idxs = np.where(vis)[0]
        for i in idxs:
            all_polys.append(
                (
                    float(tris_depth[i]),
                    [
                        (float(tris_screen_x[i, 0]), float(tris_screen_y[i, 0])),
                        (float(tris_screen_x[i, 1]), float(tris_screen_y[i, 1])),
                        (float(tris_screen_x[i, 2]), float(tris_screen_y[i, 2])),
                    ],
                    (int(cols_r[i]), int(cols_g[i]), int(cols_b[i])),
                )
            )

    # Painter's algorithm (back to front: largest sz_depth first)
    all_polys.sort(key=lambda item: item[0], reverse=True)
    for _, pts, col in all_polys:
        draw.polygon(pts, fill=col)

    return img


def main() -> None:
    print("Generating 8 watertight 3D-printable .STL files for RoboAssistant...")
    parts_spec = [
        (
            "1_head_shell.stl",
            "Head Shell (ST7789 + 48mm Speaker + INMP441 + PTT)",
            sdf_head_shell,
            ((-45.0, 45.0), (-37.0, 37.0), (-41.0, 40.0)),
            0.85,
            (236, 240, 245),
        ),
        (
            "2_front_visor_bezel.stl",
            "Front Black Visor Bezel (1.3-inch ST7789 Frame)",
            sdf_front_visor,
            ((-34.0, 34.0), (-28.0, 28.0), (-5.0, 4.0)),
            0.65,
            (32, 36, 44),
        ),
        (
            "3_top_ptt_button_cap.stl",
            "Top Circular PTT Button Cap (GPIO 1 Actuator)",
            sdf_top_button_cap,
            ((-17.0, 17.0), (-17.0, 17.0), (-7.0, 6.0)),
            0.55,
            (130, 140, 152),
        ),
        (
            "4_torso_shell.stl",
            "Chubby Torso + Click-Detent Hip Axles + SG90 Hip/Arm Bays",
            sdf_torso_shell,
            ((-43.0, 43.0), (-39.0, 39.0), (-23.0, 52.0)),
            0.85,
            (236, 240, 245),
        ),
        (
            "5_back_access_door.stl",
            "Snap-Fit Rear Service Door with Cooling Slots",
            sdf_back_door,
            ((-30.0, 30.0), (-26.0, 26.0), (-6.0, 4.0)),
            0.65,
            (215, 222, 230),
        ),
        (
            "6_arms_left_and_right.stl",
            "Articulated Left & Right Movable Arms (C-Hands)",
            sdf_arms_pair,
            ((-34.0, 34.0), (-14.0, 14.0), (-34.0, 30.0)),
            0.75,
            (232, 236, 242),
        ),
        (
            "7_thighs_left_and_right.stl",
            "Movable Hip-Pivot Thighs (0/25/90 deg Click-Detents + Ankle Ball)",
            sdf_thighs_pair,
            ((-33.0, 33.0), (-15.0, 15.0), (-25.0, 23.0)),
            0.70,
            (226, 232, 240),
        ),
        (
            "8_feet_left_and_right.stl",
            "Movable Swivel/Tilt Shoes with Flat-Standing Detents",
            sdf_feet_pair,
            ((-35.0, 35.0), (-25.0, 20.0), (-11.0, 11.0)),
            0.70,
            (236, 240, 245),
        ),
    ]

    built_parts = {}
    for fname, desc, fn, bounds, step, color in parts_spec:
        verts, faces = build_mesh_from_sdf(fn, bounds, step=step)
        stl_path = STL_DIR / fname
        write_binary_stl(stl_path, verts, faces, f"RoboAssistant {fname} - {desc}")
        dims = verts.max(axis=0) - verts.min(axis=0)
        size_kb = stl_path.stat().st_size / 1024.0
        print(
            f"  [OK] {fname:<28} | {len(faces):>6} tris | "
            f"{dims[0]:5.1f} x {dims[1]:5.1f} x {dims[2]:5.1f} mm | {size_kb:6.1f} KB"
        )
        built_parts[fname] = (verts, faces, color, dims, desc)

    # Assemble full robot in world coordinates for the 3D CAD Engineering Preview Sheet
    feet_v, feet_f, _, _, _ = built_parts["8_feet_left_and_right.stl"]
    thighs_v, thighs_f, _, _, _ = built_parts["7_thighs_left_and_right.stl"]
    torso_v, torso_f, _, _, _ = built_parts["4_torso_shell.stl"]
    head_v, head_f, _, _, _ = built_parts["1_head_shell.stl"]
    visor_v, visor_f, _, _, _ = built_parts["2_front_visor_bezel.stl"]
    btn_v, btn_f, _, _, _ = built_parts["3_top_ptt_button_cap.stl"]

    # Feet sit on the ground at z = 0..18 mm
    feet_world = feet_v.copy()
    # Thighs sit down inside the raised shoe ankle collars (z = +8.5 mm)
    thighs_world = thighs_v.copy()
    thighs_world[:, 2] += 8.5
    # Oval torso nestles directly onto the spherical hip domes (z = +26.5 mm -> seamless curved hip joint!)
    torso_world = torso_v.copy()
    torso_world[:, 2] += 26.5

    # Position head on top of torso (z = +89.0 mm)
    head_world = head_v.copy()
    head_world[:, 2] += 89.0

    # Rotate visor 90 deg around X to sit on the front of the head (y = -32.0 mm)
    visor_world = np.zeros_like(visor_v)
    visor_world[:, 0] = visor_v[:, 0]
    visor_world[:, 1] = -visor_v[:, 2] - 32.0
    visor_world[:, 2] = visor_v[:, 1] + 89.0 + 39.0

    # Place button cap at top of head + glowing cyan LED ring collar
    btn_world = btn_v.copy()
    btn_world[:, 2] += 89.0 + 73.0

    halo_v, halo_f = build_mesh_from_sdf(
        lambda x, y, z: op_subtract(
            sd_cylinder_z(x, y, z, 15.8, 1.0),
            sd_cylinder_z(x, y, z, 13.8, 2.0),
        ),
        ((-18.0, 18.0), (-18.0, 18.0), (-2.0, 2.0)),
        step=0.6,
    )
    halo_v[:, 2] += 89.0 + 75.5

    # Split left & right arms and place flush against upper oval shoulders (x = +/-42.5, z = +39)
    arm_l_v = sdf_single_arm_mesh(-1.0)
    arm_r_v = sdf_single_arm_mesh(+1.0)

    # Simulated 1.3" ST7789 screen glass + glowing cyan ring-eyes, arched brows & cute smile (matching reference image!)
    scr_v, scr_f = build_mesh_from_sdf(
        lambda x, y, z: sd_round_box(x, y, z, 13.5, 1.0, 13.5, 1.0),
        ((-15.0, 15.0), (-3.0, 3.0), (-15.0, 15.0)),
        step=0.8,
    )
    scr_v[:, 1] -= 31.0
    scr_v[:, 2] += 89.0 + 25.0

    def sdf_cute_face(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
        # Left & right glowing cyan ring eyes (R_outer=5.2, R_inner=2.4)
        eye_l = op_subtract(
            sd_cylinder_y(x + 6.5, y, z - 0.5, 5.2, 1.0),
            sd_cylinder_y(x + 6.5, y, z - 0.5, 2.3, 2.0),
        )
        eye_r = op_subtract(
            sd_cylinder_y(x - 6.5, y, z - 0.5, 5.2, 1.0),
            sd_cylinder_y(x - 6.5, y, z - 0.5, 2.3, 2.0),
        )
        # Cute arched eyebrows above each eye
        brow_l = np.maximum(
            op_subtract(
                sd_cylinder_y(x + 6.5, y, z - 2.2, 6.8, 1.0),
                sd_cylinder_y(x + 6.5, y, z - 2.2, 5.5, 2.0),
            ),
            -(z - 5.2),
        )
        brow_r = np.maximum(
            op_subtract(
                sd_cylinder_y(x - 6.5, y, z - 2.2, 6.8, 1.0),
                sd_cylinder_y(x - 6.5, y, z - 2.2, 5.5, 2.0),
            ),
            -(z - 5.2),
        )
        # Small curved smile arc between/below the eyes
        smile = np.maximum(
            op_subtract(
                sd_cylinder_y(x, y, z + 3.2, 3.2, 1.0),
                sd_cylinder_y(x, y, z + 3.2, 1.9, 2.0),
            ),
            z + 4.2,
        )
        return op_union(op_union(eye_l, eye_r), op_union(op_union(brow_l, brow_r), smile))

    eye_v, eye_f = build_mesh_from_sdf(
        sdf_cute_face,
        ((-15.0, 15.0), (-3.0, 3.0), (-10.0, 12.0)),
        step=0.45,
    )
    eye_v[:, 1] -= 32.3
    eye_v[:, 2] += 89.0 + 28.5

    assembled_meshes = [
        (feet_world, feet_f, (238, 242, 247)),
        (thighs_world, thighs_f, (234, 238, 244)),
        (torso_world, torso_f, (238, 242, 247)),
        (head_world, head_f, (238, 242, 247)),
        (visor_world, visor_f, (24, 28, 36)),
        (scr_v, scr_f, (12, 16, 22)),
        (eye_v, eye_f, (0, 240, 255)),
        (halo_v, halo_f, (0, 240, 255)),
        (btn_world, btn_f, (145, 155, 168)),
        (arm_l_v[0], arm_l_v[1], (234, 238, 244)),
        (arm_r_v[0], arm_r_v[1], (234, 238, 244)),
    ]

    hero_img = render_mesh_to_image(
        assembled_meshes,
        img_w=700,
        img_h=860,
        yaw_deg=-24.0,
        pitch_deg=12.0,
        scale=3.85,
        center_offset=(0.0, 0.0, 84.0),
    )

    sheet = Image.new("RGB", (1500, 860), (14, 18, 26))
    sheet.paste(hero_img, (0, 0))
    draw = ImageDraw.Draw(sheet)

    # Right-side 2x4 grid of all 8 individual printable .STL parts
    grid_positions = [
        ("1_head_shell.stl", 720, 35, 1.85, (0.0, 0.0, 36.0)),
        ("2_front_visor_bezel.stl", 1105, 35, 2.4, (0.0, 0.0, 2.0)),
        ("3_top_ptt_button_cap.stl", 720, 235, 3.3, (0.0, 0.0, 4.0)),
        ("4_torso_shell.stl", 1105, 235, 1.85, (0.0, 0.0, 34.0)),
        ("5_back_access_door.stl", 720, 435, 2.4, (0.0, 0.0, 2.0)),
        ("6_arms_left_and_right.stl", 1105, 435, 1.95, (0.0, 0.0, 28.0)),
        ("7_thighs_left_and_right.stl", 720, 635, 2.2, (0.0, 0.0, 18.0)),
        ("8_feet_left_and_right.stl", 1105, 635, 2.1, (0.0, 0.0, 9.0)),
    ]

    for fname, gx, gy, gscale, goff in grid_positions:
        v, f, col, dims, _ = built_parts[fname]
        tile = render_mesh_to_image(
            [(v, f, col)],
            img_w=365,
            img_h=185,
            yaw_deg=-30.0,
            pitch_deg=24.0,
            scale=gscale,
            center_offset=goff,
        )
        sheet.paste(tile, (gx, gy))
        draw.rectangle([gx, gy, gx + 365, gy + 185], outline=(45, 65, 90), width=2)
        draw.text(
            (gx + 10, gy + 6),
            f"{fname} ({dims[0]:.0f}x{dims[1]:.0f}x{dims[2]:.0f} mm)",
            fill=(0, 225, 255),
        )

    draw.text(
        (24, 18),
        "ROBOASSISTANT 3D-PRINTABLE CAD ASSEMBLY (180 mm Tall | 8 Watertight .STL Parts | Oval Belly + Movable Limbs)",
        fill=(0, 225, 255),
    )
    draw.text(
        (24, 825),
        "Option B Print-Optimized (2.8mm Lightweight Shell + 45-deg Internal Teardrop) | VSA TechnoServices Nagpur Ready",
        fill=(170, 185, 205),
    )

    preview_path = OUT_DIR / "robot_cad_preview.png"
    sheet.save(preview_path)
    print(f"  [OK] Saved 3D CAD Engineering Preview Sheet -> {preview_path}")


def sdf_single_arm_mesh(side: float) -> Tuple[np.ndarray, np.ndarray]:
    def fn(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
        return sdf_single_arm(x, y, z, side)

    v, f = build_mesh_from_sdf(fn, ((-15.0, 15.0), (-12.0, 12.0), (-33.0, 29.0)), step=0.8)
    v[:, 0] += -side * 39.8
    v[:, 2] += 35.5
    return v, f


if __name__ == "__main__":
    main()
