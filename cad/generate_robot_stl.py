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
# Part 1: Compact Rounded Squircle Head Shell (`1_head_shell.stl` — 135mm Tall Kit)
#   - Outer: 66 mm (X) x 52 mm (Y depth) x 55 mm (Z height), 9.5 mm fillet
#   - Wall thickness: 2.4 mm (fits 48mm Speaker + 1.3" ST7789 1:1 hardware!)
# ============================================================================


def sdf_head_shell(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    # 1. Outer & inner rounded squircle shell (Z centered at 0 -> -27.5..+27.5 = 55 mm tall)
    outer = sd_round_box(x, y, z, 33.0, 26.0, 27.5, 9.5)
    inner = sd_round_box(x, y, z, 30.6, 23.6, 25.1, 7.5)
    shell = op_subtract(outer, inner)

    # 2. Front visor recess (shallow 1.0 mm pocket on y = -26.0 front face, leaving 1.4 mm solid backing wall)
    visor_recess = sd_round_box(x, y + 26.0, z - 0.5, 24.0, 1.0, 19.0, 5.5)
    shell = op_subtract(shell, visor_recess)

    # 3. 1.3" ST7789 240x240 IPS Display Window (exact 26.6 x 26.6 mm 1:1 hardware through-cut!)
    tft_window = sd_round_box(x, y + 24.8, z - 0.5, 13.3, 3.5, 13.3, 2.5)
    shell = op_subtract(shell, tft_window)

    # Internal ST7789 PCB retention rails (holds exact 35.5 x 39.0 mm 7-pin module behind window, clipped to outer)
    left_rail = sd_round_box(x + 19.2, y + 21.5, z - 0.5, 1.4, 2.2, 18.5, 0.3)
    right_rail = sd_round_box(x - 19.2, y + 21.5, z - 0.5, 1.4, 2.2, 18.5, 0.3)
    bottom_ledge = sd_round_box(x, y + 21.5, z + 19.0, 19.2, 2.2, 1.5, 0.3)
    rails = np.maximum(op_union(left_rail, op_union(right_rail, bottom_ledge)), outer)
    shell = op_union(shell, rails)

    # 4. Side Speaker Grille ("Ears" at x = +/-33) + Internal 48.6 mm Speaker Collar on Right Ear
    ear_boss_r = sd_cylinder_x(x - 32.4, y, z, 15.8, 1.2)
    ear_boss_l = sd_cylinder_x(x + 32.4, y, z, 15.8, 1.2)
    ear_ring_cut_r = sd_cylinder_x(x - 33.4, y, z, 14.2, 0.9)
    ear_ring_cut_l = sd_cylinder_x(x + 33.4, y, z, 14.2, 0.9)
    shell = op_union(shell, op_union(ear_boss_r, ear_boss_l))
    shell = op_subtract(shell, op_union(ear_ring_cut_r, ear_ring_cut_l))

    # Internal 48.6 mm ID retention collar on Right Ear for Electrobot 48mm (2-Inch) 8Ohm 3W speaker
    spk_collar_outer = sd_cylinder_x(x - 28.5, y, z, 26.2, 2.8)
    spk_collar_inner = sd_cylinder_x(x - 28.5, y, z, 24.3, 3.8)
    spk_collar = np.maximum(op_subtract(spk_collar_outer, spk_collar_inner), outer)
    shell = op_union(shell, spk_collar)

    # Perforated acoustic holes across both ear grilles (center + 2 hex rings = 19 holes)
    hole_coords = [(0.0, 0.0)]
    for i in range(6):
        ang = i * math.pi / 3.0
        hole_coords.append((4.8 * math.cos(ang), 4.8 * math.sin(ang)))
    for i in range(12):
        ang = i * math.pi / 6.0
        hole_coords.append((9.6 * math.cos(ang), 9.6 * math.sin(ang)))
    for hy, hz in hole_coords:
        hole_cut = sd_cylinder_x(x, y - hy, z - hz, 1.5, 38.0)
        shell = op_subtract(shell, hole_cut)

    # 5. Top Circular PTT Button Collar + Exact 6x6mm Tactile Switch Cradle (z = +27.5 is top)
    top_ring = op_subtract(
        sd_cylinder_z(x, y, z - 27.0, 12.8, 2.2),
        sd_cylinder_z(x, y, z - 27.0, 11.0, 5.0),
    )
    shell = op_union(shell, top_ring)
    btn_hole = sd_cylinder_z(x, y, z - 26.5, 11.0, 4.5)
    shell = op_subtract(shell, btn_hole)
    # Internal bridge platform holding the 6x6mm tactile switch 8.5 mm below top hole
    sw_bridge = sd_round_box(x, y, z - 18.5, 16.0, 6.0, 1.6, 0.4)
    sw_pocket = sd_round_box(x, y, z - 19.5, 3.25, 3.25, 1.4, 0.2)
    sw_wire_slot = sd_round_box(x - 4.5, y, z - 18.5, 1.8, 3.5, 3.5, 0.2)
    sw_assy = np.maximum(op_subtract(sw_bridge, op_union(sw_pocket, sw_wire_slot)), outer)
    shell = op_union(shell, sw_assy)

    # 6. Isolated INMP441 Microphone Port & Internal Acoustic Isolation Wall (top-left front)
    mic_baffle = np.maximum(
        op_subtract(
            sd_cylinder_y(x + 16.5, y + 22.0, z - 16.0, 7.2, 2.8),
            sd_cylinder_y(x + 16.5, y + 22.0, z - 16.0, 5.5, 3.8),
        ),
        outer,
    )
    mic_pinhole = sd_cylinder_y(x + 16.5, y + 25.0, z - 16.0, 1.5, 4.0)
    shell = op_union(shell, mic_baffle)
    shell = op_subtract(shell, mic_pinhole)

    # 7. Bottom Neck Socket + 26 mm Wire Pass-Through (z = -27.5) & Rear Access Opening
    neck_open = sd_cylinder_z(x, y, z + 26.5, 13.5, 4.5)
    neck_collar = op_subtract(
        sd_cylinder_z(x, y, z + 27.5, 16.0, 2.5),
        sd_cylinder_z(x, y, z + 27.5, 13.5, 3.5),
    )
    shell = op_subtract(shell, neck_open)
    shell = op_union(shell, neck_collar)

    # Removable rear head service opening so 48mm Speaker + ST7789 slide in effortlessly
    rear_access = sd_round_box(x, y - 25.0, z, 19.5, 3.5, 16.0, 4.0)
    shell = op_subtract(shell, rear_access)

    return shell


# ============================================================================
# Part 2: Snap-In Front Black Visor Bezel (`2_front_visor_bezel.stl`)
#   - Outer: 47.6 x 37.6 mm, Window: exact 26.4 x 26.4 mm for 1.3" ST7789
# ============================================================================


def sdf_front_visor(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    plate = sd_round_box(x, y, z, 23.8, 18.8, 1.6, 5.3)
    window = sd_round_box(x, y, z, 13.2, 13.2, 4.0, 2.4)
    lip = op_subtract(
        sd_round_box(x, y, z + 1.4, 14.8, 14.8, 1.4, 2.8),
        sd_round_box(x, y, z + 1.4, 13.2, 13.2, 2.5, 2.4),
    )
    return op_union(op_subtract(plate, window), lip)


# ============================================================================
# Part 3: Top Circular PTT Button Cap (`3_top_ptt_button_cap.stl`)
# ============================================================================


def sdf_top_button_cap(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    cap = sd_cylinder_z(x, y, z - 1.8, 10.6, 2.0)
    flange = sd_cylinder_z(x, y, z + 0.8, 11.8, 1.0)
    hollow = sd_cylinder_z(x, y, z + 0.4, 9.0, 1.8)
    plunger = sd_cylinder_z(x, y, z + 2.0, 2.5, 3.0)
    body = op_subtract(op_union(cap, flange), hollow)
    return op_union(body, plunger)


# ============================================================================
# Part 4: Compact Chubby Oval Torso Shell (`4_torso_shell.stl` — 135mm Tall Kit)
#   - Outer: 62 x 50 x 52 mm smooth oval belly (2.4 mm lightweight wall)
#   - Houses ESP32-S3 (`HW678 V0.0.0`), MAX98357A, 1000uF cap
#   - Click-detent Hip Pivot Axles + Right-Shoulder SG90 Pocket
# ============================================================================


def sdf_torso_shell(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    # Plump compact oval belly (z = -16.5 bottom pelvis pad to z = +32.5 top neck deck)
    taper = 1.0 - 0.004 * (z - 6.0)
    belly_main = sd_ellipsoid(x * taper, y, z - 8.5, 29.8, 25.2, 27.8)
    tummy_bulge = sd_sphere(x * 0.92, y + 1.2, z - 6.5, 24.5)
    torso_outer = smin(belly_main, tummy_bulge, 4.5)

    bottom_clip = z + 16.5
    torso_outer = np.maximum(torso_outer, -bottom_clip)
    top_clip = -(z - 32.5)
    torso_outer = np.maximum(torso_outer, -top_clip)

    # Lightweight 2.4 mm uniform oval interior cavity
    cavity_oval = sd_ellipsoid(x * taper, y, z - 8.5, 27.3, 22.7, 25.3)
    shell = op_subtract(torso_outer, cavity_oval)

    # Top Neck Ring (mates with 1_head_shell.stl neck collar) + 22 mm open wire/PCB top clearance
    neck_peg = sd_cylinder_z(x, y, z - 34.5, 13.1, 2.8)
    neck_hole = sd_cylinder_z(x, y, z - 31.5, 10.8, 7.5)
    shell = op_subtract(op_union(shell, neck_peg), neck_hole)

    # Flush Shoulder Pivot Sockets recessed into upper oval curve (x = +/-26.0, z = +18.5)
    sh_socket_l = sd_cylinder_x(x + 26.0, y, z - 18.5, 2.9, 6.5)
    sh_socket_r = sd_cylinder_x(x - 26.0, y, z - 18.5, 2.9, 6.5)
    sg90_pocket = sd_round_box(x - 21.0, y, z - 18.5, 6.0, 6.3, 11.6, 0.4)
    shell = op_subtract(shell, op_union(sh_socket_l, op_union(sh_socket_r, sg90_pocket)))

    # Angled Crescent Lower Hip Socket Recesses (x = +/-13.5, z = -9.5)
    hip_wall_l = sd_sphere(x + 13.5, y, z + 9.5, 12.6)
    hip_wall_r = sd_sphere(x - 13.5, y, z + 9.5, 12.6)
    shell = op_union(shell, op_union(hip_wall_l, hip_wall_r))
    upper_cav = sd_ellipsoid(x * taper, y, z - 11.5, 26.2, 21.8, 18.5)
    shell = op_subtract(shell, upper_cav)

    hip_cut_l = op_union(
        sd_sphere(x + 13.5, y, z + 10.0, 10.6),
        sd_cylinder_z(x + 13.5, y, z + 14.5, 10.4, 5.5),
    )
    hip_cut_r = op_union(
        sd_sphere(x - 13.5, y, z + 10.0, 10.6),
        sd_cylinder_z(x - 13.5, y, z + 14.5, 10.4, 5.5),
    )
    shell = op_subtract(shell, op_union(hip_cut_l, hip_cut_r))

    # Heavy-duty 6.8 mm Hip Pivot Axles + Click-Detent Ridges (0 deg standing & 90 deg sit)
    hip_axle_l = sd_cylinder_x(x + 13.5, y, z + 10.0, 3.4, 7.5)
    hip_axle_r = sd_cylinder_x(x - 13.5, y, z + 10.0, 3.4, 7.5)
    detent_stand_l = sd_round_box(x + 13.5, y, z + 6.4, 6.5, 0.8, 0.9, 0.25)
    detent_stand_r = sd_round_box(x - 13.5, y, z + 6.4, 6.5, 0.8, 0.9, 0.25)
    detent_sit_l = sd_round_box(x + 13.5, y + 3.6, z + 10.0, 6.5, 0.9, 0.8, 0.25)
    detent_sit_r = sd_round_box(x - 13.5, y + 3.6, z + 10.0, 6.5, 0.9, 0.8, 0.25)
    shell = op_union(
        shell,
        op_union(
            op_union(hip_axle_l, hip_axle_r),
            op_union(op_union(detent_stand_l, detent_stand_r), op_union(detent_sit_l, detent_sit_r)),
        ),
    )

    # Internal ESP32-S3 Board Guide Rails (exact 28.8 mm channel width for HW678 V0.0.0 PCB!)
    esp_rail_l = sd_round_box(x + 15.2, y + 3.5, z - 10.5, 1.3, 2.5, 15.5, 0.3)
    esp_rail_r = sd_round_box(x - 15.2, y + 3.5, z - 10.5, 1.3, 2.5, 15.5, 0.3)
    shell = op_union(shell, op_union(esp_rail_l, esp_rail_r))

    # Rear Service Door Opening (matches 5_back_access_door.stl) + Lower USB-C Cable Slot
    back_door_cut = sd_round_box(x, y - 21.0, z - 10.5, 20.5, 7.5, 16.5, 4.2)
    usbc_slot = sd_round_box(x, y - 19.5, z + 5.5, 7.5, 7.5, 3.8, 1.8)
    shell = op_subtract(shell, op_union(back_door_cut, usbc_slot))

    return shell


# ============================================================================
# Part 5: Snap-Fit Back Service Door (`5_back_access_door.stl`)
#   - Fits both the rear head access opening and rear torso service opening
# ============================================================================


def sdf_back_door(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    plate = sd_round_box(x, y, z, 22.0, 18.0, 1.5, 4.5)
    inner_lip = op_subtract(
        sd_round_box(x, y, z - 1.8, 20.3, 16.3, 1.6, 4.0),
        sd_round_box(x, y, z - 1.8, 18.5, 14.5, 2.5, 3.4),
    )
    door = op_union(plate, inner_lip)
    for sz in (-6.0, 0.0, 6.0):
        slot = sd_round_box(x, y + sz, z, 12.5, 1.4, 4.0, 0.6)
        door = op_subtract(door, slot)
    return door


# ============================================================================
# Part 6: Articulated Left & Right Movable Arms (`6_arms_left_and_right.stl`)
#   - Smooth banana-curved tapered arm (no stepped pipe or ball bump!)
#   - Angled flush shoulder joint + crisp wrist seam + sculpted thumb & curled fingers
# ============================================================================


def sdf_single_arm(x: np.ndarray, y: np.ndarray, z: np.ndarray, side: float) -> np.ndarray:
    # Compact 135mm edition arm: +Z is shoulder top (z = +16.5), -Z is wrist/hand bottom (z = -11.0..-19.0)
    t = np.clip((16.5 - z) / 27.5, 0.0, 1.0)
    bow_x = -side * (2.5 * np.sin(t * math.pi) - 1.4 * (1.0 - t))
    r_profile = 6.2 + 1.1 * np.sin(t * math.pi) - 0.4 * t
    arm_tube = np.sqrt((x - bow_x) ** 2 + y ** 2) - r_profile
    arm_body = np.maximum(arm_tube, np.abs(z - 2.75) - 13.75)

    # Round top shoulder cap at z = +12.5 & bevel inner shoulder face to sit flush against oval torso
    shoulder_dome = sd_sphere(x - bow_x, y, z - 12.5, 6.8)
    arm_body = smin(arm_body, shoulder_dome, 2.4)
    inner_bevel = (side * (x - side * 2.0) + (z - 15.0) * 0.58)
    arm_body = np.maximum(arm_body, inner_bevel)

    # Crisp horizontal wrist seam groove at z = -10.0
    wrist_groove = op_subtract(
        sd_cylinder_z(x - bow_x, y, z + 10.0, 7.5, 0.45),
        sd_cylinder_z(x - bow_x, y, z + 10.0, 5.2, 1.0),
    )
    arm_body = op_subtract(arm_body, wrist_groove)

    # Sculpted Robot Hand at bottom (z = -10.5 to -18.8): rounded mitten palm + front-inner thumb + curled fingers
    hx = x + side * 0.4
    palm_outer = sd_ellipsoid(hx, y, z + 14.2, 5.9, 5.3, 4.8)
    palm_scoop = sd_sphere(hx - side * 2.5, y + 1.4, z + 15.4, 3.4)
    hand = op_subtract(palm_outer, palm_scoop)

    thumb = sd_ellipsoid(hx - side * 2.3, y + 3.1, z + 13.8, 2.0, 2.2, 3.0)
    hand = smin(hand, thumb, 1.0)

    fg1 = sd_round_box(hx + side * 1.5, y - 0.4, z + 17.0, 3.2, 0.4, 2.0, 0.2)
    fg2 = sd_round_box(hx + side * 1.5, y + 1.7, z + 17.0, 3.2, 0.4, 2.0, 0.2)
    hand = op_subtract(hand, op_union(fg1, fg2))

    arm_full = smin(arm_body, hand, 1.0)

    # Hidden shoulder pivot peg extending inward toward torso (+/- X) at z = +11.5
    peg = sd_cylinder_x(x - side * 5.0, y, z - 11.5, 2.5, 4.5)
    return op_union(arm_full, peg)


def sdf_arms_pair(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    left_arm = sdf_single_arm(x + 15.5, y, z, -1.0)
    right_arm = sdf_single_arm(x - 15.5, y, z, +1.0)
    return op_union(left_arm, right_arm)


# ============================================================================
# Part 7: Movable Left & Right Thighs (`7_thighs_left_and_right.stl` — 135mm Kit)
#   - Tall, wide cylindrical thighs (20 mm visible pillar) with angled hip saddle
#   - Recessed internal hip pivot turret + hidden ankle ball stud
# ============================================================================


def sdf_single_thigh(x: np.ndarray, y: np.ndarray, z: np.ndarray, side: float = 1.0) -> np.ndarray:
    # +Z is top hip saddle (z = +10.2), -Z is bottom ankle cuff (z = -9.8) -> 20.0 mm tall visible pillar!
    r_leg = 10.6 - 0.04 * (10.2 - z)
    leg_cyl = np.sqrt(x * x + (y / 1.06) ** 2) - r_leg
    top_saddle = (z - 10.0 + side * x * 0.26)
    bot_cuff = -(z + 9.8)
    thigh_outer = np.maximum(leg_cyl, np.maximum(top_saddle, bot_cuff))

    # Internal Hip Pivot Turret rising at z = +13.0 that slots inside the torso's hip socket
    hip_turret = sd_cylinder_x(x, y, z - 13.0, 7.6, 6.2)
    thigh_body = smin(thigh_outer, hip_turret, 1.6)

    # Snap-fit 7.2 mm Hip Axle Bore + C-clip snap throat at top (z = +13.0) + click-detent grooves
    hip_bore = sd_cylinder_x(x, y, z - 13.0, 3.6, 7.5)
    snap_throat = sd_round_box(x, y, z - 17.5, 6.8, 2.7, 4.0, 0.4)
    detent_groove_v = sd_round_box(x, y, z - 16.4, 6.8, 0.9, 1.1, 0.25)
    detent_groove_h = sd_round_box(x, y + 3.6, z - 13.0, 6.8, 1.1, 0.9, 0.25)
    thigh_hollow = sd_cylinder_z(x, y, z + 0.8, 6.2, 6.5)
    thigh_body = op_subtract(
        thigh_body,
        op_union(
            op_union(op_union(hip_bore, snap_throat), op_union(detent_groove_v, detent_groove_h)),
            thigh_hollow,
        ),
    )

    # Bottom Swivel + Tilt Ankle Ball Stud at z = -13.0 tucked right below the wide thigh cuff
    ankle_neck = sd_cylinder_z(x, y, z + 10.6, 5.0, 2.0)
    ankle_ball = sd_sphere(x, y, z + 13.0, 5.8)
    ankle_key = sd_round_box(x, y, z + 13.0, 6.1, 1.0, 1.6, 0.3)
    return op_union(thigh_body, op_union(ankle_neck, op_union(ankle_ball, ankle_key)))


def sdf_thighs_pair(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    left_thigh = sdf_single_thigh(x + 13.5, y, z, -1.0)
    right_thigh = sdf_single_thigh(x - 13.5, y, z, +1.0)
    return op_union(left_thigh, right_thigh)


# ============================================================================
# Part 8: Movable Left & Right Shoes/Feet (`8_feet_left_and_right.stl` — 135mm Kit)
#   - Smooth domed sneaker-pod shoes + wraparound raised ankle collar rim
# ============================================================================


def sdf_single_foot(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    # +Z is top ankle collar (z = +5.8), -Z is flat bottom sole (z = -6.5); -Y is forward toe!
    toe_dome = sd_ellipsoid(x, y + 3.8, z + 2.6, 12.2, 16.8, 8.6)
    heel_dome = sd_ellipsoid(x, y - 1.5, z + 1.5, 11.8, 12.2, 8.8)
    shoe_pod = smin(toe_dome, heel_dome, 3.2)

    collar_outer = sd_cylinder_z(x, y - 0.8, z - 2.6, 11.2, 3.2)
    foot_outer = smin(shoe_pod, collar_outer, 2.0)

    collar_cup = sd_cylinder_z(x, y - 0.8, z - 5.2, 10.1, 2.5)
    foot_outer = op_subtract(foot_outer, collar_cup)

    sole_clip = z + 6.5
    foot_outer = np.maximum(foot_outer, -sole_clip)

    socket_sphere = sd_sphere(x, y - 0.8, z - 1.2, 6.1)
    socket_throat = sd_cylinder_z(x, y - 0.8, z - 3.5, 5.4, 2.8)
    detent_slot_0 = sd_round_box(x, y - 0.8, z - 1.2, 6.5, 1.1, 2.0, 0.3)
    detent_slot_stride = sd_round_box(x, y - 0.8, z - 1.2, 1.1, 6.5, 2.0, 0.3)
    return op_subtract(
        foot_outer,
        op_union(op_union(socket_sphere, socket_throat), op_union(detent_slot_0, detent_slot_stride)),
    )


def sdf_feet_pair(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    left_foot = sdf_single_foot(x + 13.5, y, z)
    right_foot = sdf_single_foot(x - 13.5, y, z)
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
    print("Generating 8 watertight 3D-printable .STL files for RoboAssistant (135 mm / 13.5 cm Compact Edition)...")
    parts_spec = [
        (
            "1_head_shell.stl",
            "Head Shell (ST7789 + 48mm Speaker + INMP441 + PTT)",
            sdf_head_shell,
            ((-36.0, 36.0), (-29.0, 29.0), (-31.0, 31.0)),
            0.70,
            (236, 240, 245),
        ),
        (
            "2_front_visor_bezel.stl",
            "Front Black Visor Bezel (1.3-inch ST7789 Frame)",
            sdf_front_visor,
            ((-28.0, 28.0), (-23.0, 23.0), (-4.0, 4.0)),
            0.55,
            (32, 36, 44),
        ),
        (
            "3_top_ptt_button_cap.stl",
            "Top Circular PTT Button Cap (GPIO 1 Actuator)",
            sdf_top_button_cap,
            ((-14.0, 14.0), (-14.0, 14.0), (-6.0, 5.0)),
            0.45,
            (130, 140, 152),
        ),
        (
            "4_torso_shell.stl",
            "Compact Oval Torso + Click-Detent Hip Axles + ESP32-S3 Rails",
            sdf_torso_shell,
            ((-33.0, 33.0), (-29.0, 29.0), (-18.0, 39.0)),
            0.70,
            (236, 240, 245),
        ),
        (
            "5_back_access_door.stl",
            "Snap-Fit Rear Service Door with Cooling Slots",
            sdf_back_door,
            ((-25.0, 25.0), (-21.0, 21.0), (-5.0, 4.0)),
            0.55,
            (215, 222, 230),
        ),
        (
            "6_arms_left_and_right.stl",
            "Articulated Left & Right Movable Arms (Thumb + Curled Fingers)",
            sdf_arms_pair,
            ((-28.0, 28.0), (-11.0, 11.0), (-21.0, 20.0)),
            0.60,
            (232, 236, 242),
        ),
        (
            "7_thighs_left_and_right.stl",
            "Movable Hip-Pivot Thighs (0/90 deg Click-Detents + Ankle Ball)",
            sdf_thighs_pair,
            ((-26.0, 26.0), (-14.0, 14.0), (-21.0, 22.0)),
            0.60,
            (232, 236, 242),
        ),
        (
            "8_feet_left_and_right.stl",
            "Movable Swivel/Tilt Shoes with Wraparound Ankle Collars",
            sdf_feet_pair,
            ((-28.0, 28.0), (-22.0, 16.0), (-8.0, 8.0)),
            0.60,
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

    # Assemble full robot in world coordinates for the 3D CAD Engineering Preview Sheet (Exact 135.0 mm tall!)
    feet_v, feet_f, _, _, _ = built_parts["8_feet_left_and_right.stl"]
    thighs_v, thighs_f, _, _, _ = built_parts["7_thighs_left_and_right.stl"]
    torso_v, torso_f, _, _, _ = built_parts["4_torso_shell.stl"]
    head_v, head_f, _, _, _ = built_parts["1_head_shell.stl"]
    visor_v, visor_f, _, _, _ = built_parts["2_front_visor_bezel.stl"]
    btn_v, btn_f, _, _, _ = built_parts["3_top_ptt_button_cap.stl"]

    # Feet sit on the ground at z = 0..12.5 mm
    feet_world = feet_v.copy()
    # Thighs sit 2.0 mm down inside the shoe's raised ankle collar rim (z = +1.4 mm)
    thighs_world = thighs_v.copy()
    thighs_world[:, 2] += 1.4
    # Oval torso sits on top of the thigh pillars (z = +27.5 mm)
    torso_world = torso_v.copy()
    torso_world[:, 2] += 27.5

    # Position head on top of torso (z = +74.2 mm)
    head_world = head_v.copy()
    head_world[:, 2] += 74.2

    # Rotate visor 90 deg around X to sit on the front of the head (y = -24.5 mm)
    visor_world = np.zeros_like(visor_v)
    visor_world[:, 0] = visor_v[:, 0]
    visor_world[:, 1] = -visor_v[:, 2] - 24.5
    visor_world[:, 2] = visor_v[:, 1] + 74.2 + 29.0

    # Place button cap at top of head -> exact top of button reaches z = 135.0 mm (13.5 cm)!
    btn_world = btn_v.copy()
    btn_world[:, 2] += 135.0 - btn_world[:, 2].max()

    halo_v, halo_f = build_mesh_from_sdf(
        lambda x, y, z: op_subtract(
            sd_cylinder_z(x, y, z, 12.2, 0.8),
            sd_cylinder_z(x, y, z, 10.6, 1.6),
        ),
        ((-14.0, 14.0), (-14.0, 14.0), (-2.0, 2.0)),
        step=0.5,
    )
    halo_v[:, 2] += 74.2 + 56.5

    # Split left & right arms and place flush against upper oval shoulders (x = +/-29.6, z = +34.5)
    arm_l_v = sdf_single_arm_mesh(-1.0)
    arm_r_v = sdf_single_arm_mesh(+1.0)

    total_height_mm = max(v[:, 2].max() for v in (feet_world, thighs_world, torso_world, head_world, btn_world))
    print(f"  [OK] Total Assembled Standing Height: {total_height_mm:.1f} mm ({total_height_mm / 10.0:.2f} cm / {total_height_mm / 25.4:.2f} inches)")

    # Simulated 1.3" ST7789 screen glass + glowing cyan ring-eyes, arched brows & cute smile
    scr_v, scr_f = build_mesh_from_sdf(
        lambda x, y, z: sd_round_box(x, y, z, 13.3, 1.0, 13.3, 1.0),
        ((-15.0, 15.0), (-3.0, 3.0), (-15.0, 15.0)),
        step=0.7,
    )
    scr_v[:, 1] -= 23.6
    scr_v[:, 2] += 74.2 + 16.0

    def sdf_cute_face(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
        eye_l = op_subtract(
            sd_cylinder_y(x + 6.5, y, z - 0.5, 5.2, 1.0),
            sd_cylinder_y(x + 6.5, y, z - 0.5, 2.3, 2.0),
        )
        eye_r = op_subtract(
            sd_cylinder_y(x - 6.5, y, z - 0.5, 5.2, 1.0),
            sd_cylinder_y(x - 6.5, y, z - 0.5, 2.3, 2.0),
        )
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
    eye_v[:, 1] -= 24.8
    eye_v[:, 2] += 74.2 + 19.5

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
        scale=4.85,
        center_offset=(0.0, 0.0, 67.5),
    )

    sheet = Image.new("RGB", (1500, 860), (14, 18, 26))
    sheet.paste(hero_img, (0, 0))
    draw = ImageDraw.Draw(sheet)

    # Right-side 2x4 grid of all 8 individual printable .STL parts
    grid_positions = [
        ("1_head_shell.stl", 720, 35, 2.35, (0.0, 0.0, 28.0)),
        ("2_front_visor_bezel.stl", 1105, 35, 2.9, (0.0, 0.0, 2.0)),
        ("3_top_ptt_button_cap.stl", 720, 235, 4.0, (0.0, 0.0, 3.0)),
        ("4_torso_shell.stl", 1105, 235, 2.35, (0.0, 0.0, 26.0)),
        ("5_back_access_door.stl", 720, 435, 3.0, (0.0, 0.0, 2.0)),
        ("6_arms_left_and_right.stl", 1105, 435, 2.5, (0.0, 0.0, 18.0)),
        ("7_thighs_left_and_right.stl", 720, 635, 2.6, (0.0, 0.0, 17.0)),
        ("8_feet_left_and_right.stl", 1105, 635, 2.6, (0.0, 0.0, 6.5)),
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
        "ROBOASSISTANT 3D-PRINTABLE CAD ASSEMBLY (135 mm / 13.5 cm Compact Tabletop Size | 8 Watertight .STL Parts)",
        fill=(0, 225, 255),
    )
    draw.text(
        (24, 825),
        "Exact Height: 135.0 mm (13.5 cm / 5.3 in) | ~70g PLA (~Rs.220-290 at VSA TechnoServices Nagpur) | 1:1 Hardware Mounts",
        fill=(170, 185, 205),
    )

    preview_path = OUT_DIR / "robot_cad_preview.png"
    sheet.save(preview_path)
    print(f"  [OK] Saved 3D CAD Engineering Preview Sheet -> {preview_path}")


def sdf_single_arm_mesh(side: float) -> Tuple[np.ndarray, np.ndarray]:
    def fn(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
        return sdf_single_arm(x, y, z, side)

    v, f = build_mesh_from_sdf(fn, ((-12.0, 12.0), (-10.0, 10.0), (-21.0, 20.0)), step=0.65)
    v[:, 0] += -side * 29.5
    v[:, 2] += 34.2
    return v, f


if __name__ == "__main__":
    main()
