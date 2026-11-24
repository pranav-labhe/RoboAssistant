"""
RoboAssistant Desktop Companion — Hero-Matched 3D Printable CAD (.STL) Generator
============================================================================
Generates 9 watertight, manifold binary .STL files fitted to the measured hardware & 20 cm wire mesh
with +1.6..+2.4 mm print-error oversize tolerances, compliant spring-crush ribs, and snap-locks:

  1. 1_head_shell.stl            - Pure egg head (~89x62x70 mm), oval visor, left-ear INMP441 bay, GMT130, PTT & wire hooks
  2. 2_front_visor_bezel.stl     - Seamless 3D curved oval obsidian visor bezel (60x41 mm) + 4-barb snap fit
  3. 3_top_ptt_button_cap.stl    - Top PTT plunger cap w/ 4-petal compliant flange
  4. 4_torso_shell.stl           - Pure teardrop egg belly (~83x91x70 mm): forward SUNVOICE, chest grille, flank amp/hubs, SG90 & LiPo bay
  5. 5_back_access_door.stl      - Flush 3D curved oval ESP32-S3 rear dock (open middle pins, USB-C notch, 6-barb lock)
  6. 6_arms_left_and_right.stl   - Plump ~47 mm curved arms w/ split-slot shoulder pegs & snap beads
  7. 7_thighs_left_and_right.stl - Movable hip-pivot thighs w/ dual 0/90 deg click-detents & ankle snap ball
  8. 8_feet_left_and_right.stl   - Extended anti-tip flat-sole sneaker shoes w/ zero-pitch ankle lock
  9. 9_head_back_cap.stl         - Flush 3D curved rear head snap-in cover sealing the head wire-coil cavern

Also renders `cad/robot_cad_preview.png` so you can inspect all 9 meshes visually.
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
PRINT_MESH_STEP_MM = 0.35  # printable-part marching-cubes grid (sharper edges than 0.40 mm nozzle)


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


def sd_round_plate_z(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    bx: float,
    by: float,
    bz: float,
    r: float,
) -> np.ndarray:
    """2D rounded rectangle in XY extruded cleanly along Z without shrinking thin plates."""
    qx = np.abs(x) - (bx - r)
    qy = np.abs(y) - (by - r)
    d2 = np.sqrt(np.maximum(qx, 0.0) ** 2 + np.maximum(qy, 0.0) ** 2) + np.minimum(
        np.maximum(qx, qy), 0.0
    ) - r
    return np.maximum(d2, np.abs(z) - bz)


def sd_round_plate_y(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    bx: float,
    by: float,
    bz: float,
    r: float,
) -> np.ndarray:
    """2D rounded rectangle in XZ extruded cleanly along Y without shrinking recesses."""
    qx = np.abs(x) - (bx - r)
    qz = np.abs(z) - (bz - r)
    d2 = np.sqrt(np.maximum(qx, 0.0) ** 2 + np.maximum(qz, 0.0) ** 2) + np.minimum(
        np.maximum(qx, qz), 0.0
    ) - r
    return np.maximum(d2, np.abs(y) - by)


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
# Part 1: 100% Pure Egg Companion Head (`1_head_shell.stl` — ~90x69x72 mm)
#   - Zero square/box walls: pure smooth ellipsoid/sphere egg + spherical ear pods
#   - Concentric 3D curved oval visor recess (61 x 42 mm, r = 20.8 mm)
#   - Oversize (+1.6 to +2.4 mm) pockets + compliant spring-crush ribs & snap-locks:
#       1) GMT130-V1.0 Display (28.0x39.0x18.5 mm w/ plugs, +2.2 mm eye offset)
#       2) INMP441 Round Mic (Ø14.0 mm disk, 17.5 mm plugs, left-cheek oval bay)
#       3) 6x6x5 mm Red Tactile Switch (8.0 mm across legs, 2 open plug chutes)
#       4) Neck Snap-Lock Collar (Ø34.4 mm bore + 33x21 mm spine slot)
# ============================================================================


def egg_head_outer(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    taper_h = 1.0 + 0.0025 * z
    dome_main = sd_ellipsoid(x * taper_h, y, z, 43.5, 33.5, 34.0)
    cheek_swell = sd_sphere(x * 0.84, y + 1.0, z + 1.0, 33.8)
    outer = smin(dome_main, cheek_swell, 4.5)
    ear_pod_r = sd_sphere(x - 32.5, y - 0.5, z, 13.0)
    ear_pod_l = sd_sphere(x + 32.5, y - 0.5, z, 13.0)
    return smin(outer, op_union(ear_pod_r, ear_pod_l), 2.0)


def sdf_head_shell(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    # 1. Pure Ellipsoid/Sphere Egg Shell (zero flat box edges!)
    outer = egg_head_outer(x, y, z)
    taper_h = 1.0 + 0.0025 * z
    inner_main = sd_ellipsoid(x * taper_h, y - 1.2, z, 41.1, 30.2, 31.6)
    inner_swell = sd_sphere(x * 0.84, y - 0.2, z + 1.0, 31.4)
    inner = smin(inner_main, inner_swell, 4.0)
    shell = op_subtract(outer, inner)

    # Smooth concave spherical ear dishes (stays >2 mm outside inner 41.1 wall) + micro-dimples
    ear_scoop_r = sd_sphere(x - 54.0, y - 0.5, z, 10.8)
    ear_scoop_l = sd_sphere(x + 54.0, y - 0.5, z, 10.8)
    shell = op_subtract(shell, op_union(ear_scoop_r, ear_scoop_l))

    hole_coords = [(0.0, 0.0)]
    for i in range(6):
        ang = i * math.pi / 3.0
        hole_coords.append((4.0 * math.cos(ang), 4.0 * math.sin(ang)))
    for i in range(12):
        ang = i * math.pi / 6.0
        hole_coords.append((8.0 * math.cos(ang), 8.0 * math.sin(ang)))
    for hy, hz in hole_coords:
        dimple_r = sd_cylinder_x(x - 43.5, y - (0.5 + hy), z - hz, 0.75, 1.8)
        dimple_l = sd_cylinder_x(x + 43.5, y - (0.5 + hy), z - hz, 0.85, 5.0)
        shell = op_subtract(shell, op_union(dimple_r, dimple_l))

    # 2. 3D Concentric Curved Oval-Capsule Visor Recess (61.6 x 42.4 mm, r = 20.9 mm) + 26.8x26.8 mm Window
    visor_oval_prism = sd_round_plate_y(x, y + 24.0, z, 30.8, 16.0, 21.2, 20.9)
    visor_recess = np.maximum(visor_oval_prism, -(outer + 2.15))
    shell = op_subtract(shell, visor_recess)

    tft_window = sd_round_plate_y(x, y + 29.0, z, 13.4, 6.0, 13.4, 3.0)
    shell = op_subtract(shell, tft_window)

    # GMT130-V1.0 Oversize Lock Cradle tucked safely behind visor recess (cz = +2.2 mm PCB offset)
    disp_rail_l = op_subtract(
        sd_round_box(x + 16.2, y + 23.6, z - 2.2, 2.0, 2.8, 21.0, 0.3),
        sd_round_box(x + 14.5, y + 24.0, z - 2.2, 1.5, 1.3, 21.0, 0.2),
    )
    disp_rail_r = op_subtract(
        sd_round_box(x - 16.2, y + 23.6, z - 2.2, 2.0, 2.8, 21.0, 0.3),
        sd_round_box(x - 14.5, y + 24.0, z - 2.2, 1.5, 1.3, 21.0, 0.2),
    )
    disp_ribs = np.full_like(x, 1e6)
    for rz in (-9.8, 2.2, 14.2):
        rib_l = sd_round_box(x + 14.45, y + 23.4, z - rz, 0.55, 0.65, 2.0, 0.15)
        rib_r = sd_round_box(x - 14.45, y + 23.4, z - rz, 0.55, 0.65, 2.0, 0.15)
        pinch_l = sd_round_box(x + 14.7, y + 22.8, z - rz, 0.6, 0.5, 2.2, 0.15)
        pinch_r = sd_round_box(x - 14.7, y + 22.8, z - rz, 0.6, 0.5, 2.2, 0.15)
        disp_ribs = op_union(disp_ribs, op_union(op_union(rib_l, rib_r), op_union(pinch_l, pinch_r)))

    disp_bot_ledge = sd_round_box(x, y + 23.6, z + 19.3, 16.0, 2.6, 1.2, 0.3)
    disp_top_latch = sd_round_box(x, y + 23.0, z - 23.6, 8.5, 2.5, 1.1, 0.3)
    disp_assy = np.maximum(
        op_union(op_union(disp_rail_l, disp_rail_r), op_union(disp_ribs, op_union(disp_bot_ledge, disp_top_latch))),
        outer + 2.2,
    )
    disp_assy = op_subtract(disp_assy, visor_recess)
    shell = op_union(shell, disp_assy)

    # 3. Centered Crown PTT Button Collar + Oversize 7.6x7.6 mm Switch Pocket (cx = 0.0, cy = +4.5, cz = +33.0)
    top_ring = op_subtract(
        sd_cylinder_z(x, y - 4.5, z - 33.0, 13.5, 2.2),
        sd_cylinder_z(x, y - 4.5, z - 33.0, 11.2, 5.0),
    )
    shell = op_union(shell, top_ring)
    shell = op_subtract(shell, sd_cylinder_z(x, y - 4.5, z - 32.5, 11.2, 4.5))

    sw_bridge = sd_round_box(x, y - 4.5, z - 25.2, 19.0, 8.0, 2.4, 0.5)
    sw_pocket = sd_round_box(x, y - 4.5, z - 26.8, 3.8, 3.8, 1.8, 0.2)
    sw_plug_chute_f = sd_round_box(x, y - 1.2, z - 25.2, 5.2, 1.9, 4.5, 0.3)
    sw_plug_chute_r = sd_round_box(x, y - 7.8, z - 25.2, 5.2, 1.9, 4.5, 0.3)
    sw_ribs = op_union(
        op_union(
            sd_round_box(x + 3.45, y - 4.5, z - 26.0, 0.50, 1.8, 1.2, 0.15),
            sd_round_box(x - 3.45, y - 4.5, z - 26.0, 0.50, 1.8, 1.2, 0.15),
        ),
        op_union(
            sd_round_box(x, y - 1.05, z - 26.0, 1.8, 0.50, 1.2, 0.15),
            sd_round_box(x, y - 7.95, z - 26.0, 1.8, 0.50, 1.2, 0.15),
        ),
    )
    sw_nibs = op_union(
        sd_round_box(x + 3.0, y - 4.5, z - 27.8, 0.6, 2.2, 0.5, 0.15),
        sd_round_box(x - 3.0, y - 4.5, z - 27.8, 0.6, 2.2, 0.5, 0.15),
    )
    sw_assy = np.maximum(
        op_union(
            op_subtract(sw_bridge, op_union(sw_pocket, op_union(sw_plug_chute_f, sw_plug_chute_r))),
            op_union(sw_ribs, sw_nibs),
        ),
        outer,
    )
    shell = op_union(shell, sw_assy)

    # 4. Left-Ear INMP441 Round Mic Bay (cx = -19.6, cy = +2.0, cz = +2.0, facing -X ear holes -> 20.6 mm clear!)
    mic_outer_cyl = sd_cylinder_x(x + 19.6, y - 2.0, z - 2.0, 9.6, 2.2)
    mic_bore = sd_cylinder_x(x + 20.2, y - 2.0, z - 2.0, 7.8, 2.6)
    mic_wire_exit = sd_round_box(x + 21.0, y - 6.5, z + 3.5, 4.5, 5.5, 6.0, 0.4)
    mic_socket = op_subtract(mic_outer_cyl, op_union(mic_bore, mic_wire_exit))
    mic_ribs = op_union(
        sd_round_box(x + 20.2, y - 2.0, z - 9.2, 1.8, 1.6, 0.7, 0.15),
        op_union(
            sd_round_box(x + 20.2, y - 2.0, z + 5.2, 1.8, 1.6, 0.7, 0.15),
            sd_round_box(x + 20.2, y + 5.2, z - 2.0, 1.8, 0.7, 1.6, 0.15),
        ),
    )
    mic_brace = sd_round_box(x + 18.8, y - 2.0, z - 20.0, 1.8, 2.2, 13.0, 0.4)
    mic_assy = np.maximum(op_union(op_union(mic_socket, mic_ribs), mic_brace), outer + 1.8)
    shell = op_union(shell, mic_assy)

    # Internal rear-wall wire-retention C-hooks above neck spine for 15-wire head trunk
    wire_hook_l = op_subtract(
        sd_round_box(x + 18.0, y - 20.5, z + 17.0, 2.0, 4.5, 4.0, 0.4),
        sd_round_box(x + 17.5, y - 19.0, z + 17.0, 3.2, 2.2, 2.4, 0.3),
    )
    wire_hook_r = op_subtract(
        sd_round_box(x - 18.0, y - 20.5, z + 17.0, 2.0, 4.5, 4.0, 0.4),
        sd_round_box(x - 17.5, y - 19.0, z + 17.0, 3.2, 2.2, 2.4, 0.3),
    )
    shell = op_union(shell, np.maximum(op_union(wire_hook_l, wire_hook_r), outer + 1.5))

    # 5. Bottom Neck Snap-Lock Collar (Ø34.4 mm bore + snap-groove + 33x21 mm stadium spine opening)
    neck_open = op_union(
        sd_cylinder_z(x, y, z + 32.0, 17.2, 5.0),
        sd_round_box(x, y, z + 32.0, 16.5, 10.5, 5.0, 3.5),
    )
    neck_groove = sd_cylinder_z(x, y, z + 30.8, 17.9, 1.1)
    neck_collar = op_subtract(
        sd_cylinder_z(x, y, z + 33.0, 20.5, 2.2),
        op_union(neck_open, neck_groove),
    )
    shell = op_subtract(shell, op_union(neck_open, neck_groove))
    shell = op_union(shell, neck_collar)

    # 6. Round Rear Service Opening (Ø38.0 mm at y = +31.0, sealed by 9_head_back_cap.stl)
    rear_access = sd_cylinder_y(x, y - 31.0, z, 19.0, 8.0)
    shell = op_subtract(shell, rear_access)

    return shell


# ============================================================================
# Part 2: 3D Curved Oval-Capsule Visor Bezel (`2_front_visor_bezel.stl`)
#   - Outer: 60.0 x 41.0 mm, r = 20.4 mm conforming identically to `egg_head_outer`
#   - Seamless obsidian bezel w/ 26.6 x 26.6 mm window + 4 slotted snap barbs
# ============================================================================


def sdf_front_visor(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    egg_map = egg_head_outer(x, z - 35.5, -y)
    curved_shell = np.maximum(egg_map - 0.8, -(egg_map + 1.9))
    oval_mask = sd_round_plate_z(x, y, z - 4.0, 30.0, 20.5, 10.0, 20.4)
    plate = np.maximum(curved_shell, oval_mask)

    window = sd_round_plate_z(x, y, z - 2.0, 13.3, 13.3, 10.0, 2.8)
    lip = op_subtract(
        sd_round_plate_z(x, y, z - 4.2, 14.8, 14.8, 2.4, 3.2),
        sd_round_plate_z(x, y, z - 4.2, 13.3, 13.3, 3.6, 2.8),
    )
    barbs = op_union(
        op_union(
            sd_round_box(x + 14.7, y, z - 5.6, 0.7, 5.5, 0.7, 0.2),
            sd_round_box(x - 14.7, y, z - 5.6, 0.7, 5.5, 0.7, 0.2),
        ),
        op_union(
            sd_round_box(x, y + 14.7, z - 5.6, 5.5, 0.7, 0.7, 0.2),
            sd_round_box(x, y - 14.7, z - 5.6, 5.5, 0.7, 0.7, 0.2),
        ),
    )
    return op_union(op_subtract(plate, window), op_union(lip, barbs))


# ============================================================================
# Part 9: Flush 3D Curved Rear Head Wiring Cover (`9_head_back_cap.stl`)
#   - Seals Ø38.0 mm rear head wiring & excess-wire coil chamber seamlessly
# ============================================================================


def sdf_head_back_cap(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    cap_map = egg_head_outer(x, 35.5 - z, y)
    curved = np.maximum(cap_map - 0.6, -(cap_map + 1.8))
    mask = sd_cylinder_z(x, y, z - 4.0, 20.4, 8.0)
    plate = np.maximum(curved, mask)

    lip = op_subtract(
        sd_cylinder_z(x, y, z - 6.2, 18.7, 3.2),
        sd_cylinder_z(x, y, z - 6.2, 17.1, 4.2),
    )
    barbs = op_union(
        op_union(
            sd_round_box(x + 18.9, y, z - 8.6, 0.7, 4.5, 0.7, 0.2),
            sd_round_box(x - 18.9, y, z - 8.6, 0.7, 4.5, 0.7, 0.2),
        ),
        op_union(
            sd_round_box(x, y + 18.9, z - 8.6, 4.5, 0.7, 0.7, 0.2),
            sd_round_box(x, y - 18.9, z - 8.6, 4.5, 0.7, 0.7, 0.2),
        ),
    )
    return op_union(plate, np.maximum(op_union(lip, barbs), cap_map + 0.4))


# ============================================================================
# Part 3: Top Circular PTT Button Cap (`3_top_ptt_button_cap.stl`)
#   - Slotted 4-petal compliant retention flange + wide Ø6.6 mm actuator pad
# ============================================================================


def sdf_top_button_cap(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    cap = sd_cylinder_z(x, y, z - 1.8, 10.7, 2.0)
    flange = sd_cylinder_z(x, y, z + 0.8, 11.8, 1.0)
    slot_x = sd_round_box(x, y, z + 0.8, 12.5, 0.9, 1.6, 0.2)
    slot_y = sd_round_box(x, y, z + 0.8, 0.9, 12.5, 1.6, 0.2)
    hollow = sd_cylinder_z(x, y, z + 0.4, 8.8, 1.8)
    body = op_subtract(op_union(cap, flange), op_union(hollow, op_union(slot_x, slot_y)))
    plunger = sd_cylinder_z(x, y, z + 1.8, 2.7, 2.8)
    act_pad = sd_cylinder_z(x, y, z + 4.4, 3.3, 0.8)
    return op_union(body, op_union(plunger, act_pad))


# ============================================================================
# Part 4: 100% Pure Teardrop Egg Torso (`4_torso_shell.stl` — ~86x82x70 mm)
#   - Zero `sd_round_box` on outer belly: pure smooth teardrop ellipsoid/sphere
#   - Narrower tapered shoulders than head (~77 mm at shoulder, ~86 mm lower belly)
#   - Houses SUNVOICE 3740 (52x52x38 mm) in front-center chamber + 3D oval back hatch
# ============================================================================


def egg_torso_outer(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    taper = 1.0 + 0.0022 * (z - 6.5)
    belly_main = sd_ellipsoid(x * taper, y, z - 6.5, 39.2, 41.5, 39.5)
    tummy_bulge = sd_ellipsoid(x, y + 10.5, z - 6.5, 41.6, 40.0, 42.0)
    outer = smin(belly_main, tummy_bulge, 4.5)
    outer = np.maximum(outer, -(z + 21.5))
    return np.maximum(outer, (z - 44.0))


def sdf_torso_shell(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    # 1. Pure Ellipsoid/Sphere Teardrop Belly (zero square corners, ~83 mm wide < 89 mm head)
    torso_outer = egg_torso_outer(x, y, z)

    taper = 1.0 + 0.0022 * (z - 6.5)
    cavity_main = sd_ellipsoid(x * taper, y, z - 6.5, 36.8, 39.1, 37.1)
    cavity_bulge = sd_ellipsoid(x, y + 10.5, z - 6.5, 39.2, 37.6, 39.6)
    cavity = smin(cavity_main, cavity_bulge, 4.0)
    cavity = np.maximum(cavity, -(z + 19.1))
    cavity = np.maximum(cavity, (z - 41.4))
    shell = op_subtract(torso_outer, cavity)

    # Subtle 19-dot concentric circular acoustic grille on front chest over SUNVOICE 3740 cone (cz = +6.5)
    chest_dots = [(0.0, 0.0)]
    for i in range(6):
        ang = i * math.pi / 3.0
        chest_dots.append((7.0 * math.cos(ang), 7.0 * math.sin(ang)))
    for i in range(12):
        ang = i * math.pi / 6.0
        chest_dots.append((14.0 * math.cos(ang), 14.0 * math.sin(ang)))
    for gx, gz in chest_dots:
        shell = op_subtract(shell, sd_cylinder_y(x - gx, y + 48.5, z - (6.5 + gz), 1.1, 6.0))

    # 2. Compliant Top Neck Snap-Peg (Ø33.8 mm + Ø34.6 mm annular snap-bead + compliance slots)
    neck_peg = op_union(
        sd_cylinder_z(x, y, z - 45.5, 16.9, 2.5),
        sd_cylinder_z(x, y, z - 46.3, 17.3, 0.9),
    )
    neck_spine_slot = sd_round_box(x, y, z - 43.5, 16.25, 10.25, 8.0, 3.5)
    neck_flex_slots = op_union(
        sd_round_box(x, y - 16.5, z - 46.0, 1.2, 2.5, 2.8, 0.2),
        sd_round_box(x, y + 16.5, z - 46.0, 1.2, 2.5, 2.8, 0.2),
    )
    shell = op_subtract(op_union(shell, neck_peg), op_union(neck_spine_slot, neck_flex_slots))

    # 3. Upper Shoulder Pivot Sockets (cx = +/-34.5, cz = +25.0) + Dual-Mode Right SG90 Servo Cradle
    sh_socket_l = sd_cylinder_x(x + 34.5, y, z - 25.0, 3.1, 6.5)
    sh_socket_r = sd_cylinder_x(x - 34.5, y, z - 25.0, 3.1, 6.5)
    shell = op_subtract(shell, op_union(sh_socket_l, sh_socket_r))

    sg90_box = sd_round_box(x - 27.5, y - 1.5, z - 25.0, 7.5, 8.0, 13.2, 0.4)
    sg90_slot = sd_round_box(x - 26.5, y - 1.5, z - 25.0, 8.0, 6.7, 11.9, 0.2)
    sg90_wire_open = sd_round_box(x - 20.0, y - 1.5, z - 25.0, 5.0, 5.2, 10.5, 0.2)
    sg90_ribs = op_union(
        sd_round_box(x - 27.5, y - 7.8, z - 25.0, 4.5, 0.45, 8.0, 0.15),
        sd_round_box(x - 27.5, y + 4.8, z - 25.0, 4.5, 0.45, 8.0, 0.15),
    )
    sg90_cradle = op_union(op_subtract(sg90_box, op_union(sg90_slot, sg90_wire_open)), sg90_ribs)
    sg90_cradle = op_subtract(np.maximum(sg90_cradle, torso_outer + 1.8), sh_socket_r)
    shell = op_union(shell, sg90_cradle)

    # 4. Contained Deep Crescent Hip Sockets (cx = +/-15.5, cz = -13.5) + 6.8 mm Axles + Dual Click-Detents
    hip_wall_l = sd_sphere(x + 15.5, y, z + 13.5, 13.4)
    hip_wall_r = sd_sphere(x - 15.5, y, z + 13.5, 13.4)
    shell = op_union(shell, np.maximum(op_union(hip_wall_l, hip_wall_r), torso_outer))

    hip_cut_l = op_union(
        sd_sphere(x + 15.5, y, z + 13.5, 11.2),
        sd_cylinder_z(x + 15.5, y, z + 18.0, 11.0, 5.5),
    )
    hip_cut_r = op_union(
        sd_sphere(x - 15.5, y, z + 13.5, 11.2),
        sd_cylinder_z(x - 15.5, y, z + 18.0, 11.0, 5.5),
    )
    shell = op_subtract(shell, op_union(hip_cut_l, hip_cut_r))

    hip_axle_l = sd_cylinder_x(x + 15.5, y, z + 13.5, 3.4, 8.0)
    hip_axle_r = sd_cylinder_x(x - 15.5, y, z + 13.5, 3.4, 8.0)
    detent_stand_l = sd_round_box(x + 15.5, y, z + 9.9, 6.8, 0.9, 1.0, 0.25)
    detent_stand_r = sd_round_box(x - 15.5, y, z + 9.9, 6.8, 0.9, 1.0, 0.25)
    detent_stand_b_l = sd_round_box(x + 15.5, y - 3.6, z + 13.5, 6.8, 1.0, 0.9, 0.25)
    detent_stand_b_r = sd_round_box(x - 15.5, y - 3.6, z + 13.5, 6.8, 1.0, 0.9, 0.25)
    detent_sit_l = sd_round_box(x + 15.5, y + 3.6, z + 13.5, 6.8, 1.0, 0.9, 0.25)
    detent_sit_r = sd_round_box(x - 15.5, y + 3.6, z + 13.5, 6.8, 1.0, 0.9, 0.25)
    shell = op_union(
        shell,
        op_union(
            op_union(hip_axle_l, hip_axle_r),
            op_union(
                op_union(detent_stand_l, detent_stand_r),
                op_union(op_union(detent_stand_b_l, detent_stand_b_r), op_union(detent_sit_l, detent_sit_r)),
            ),
        ),
    )

    # 5. Forward SUNVOICE 3740 Speaker Collar (54.4x54.4 mm @ cy = -24.0, cz = +6.5 -> magnet rear @ y = +7.9 mm!)
    spk_sq_outer = sd_round_box(x, y + 24.0, z - 6.5, 29.2, 3.6, 29.2, 6.0)
    spk_sq_inner = sd_round_box(x, y + 24.0, z - 6.5, 27.2, 4.8, 27.2, 4.8)
    spk_front_stop = op_subtract(
        sd_round_box(x, y + 27.0, z - 6.5, 29.2, 0.9, 29.2, 6.0),
        sd_round_box(x, y + 27.0, z - 6.5, 23.6, 1.8, 23.6, 4.5),
    )
    spk_collar = op_union(op_subtract(spk_sq_outer, spk_sq_inner), spk_front_stop)
    spk_standoff_l = sd_round_box(x + 28.5, y + 31.5, z - 6.5, 2.2, 8.0, 12.0, 0.4)
    spk_standoff_r = sd_round_box(x - 28.5, y + 31.5, z - 6.5, 2.2, 8.0, 12.0, 0.4)
    spk_standoff_t = sd_round_box(x, y + 31.0, z - 34.7, 12.0, 7.5, 2.2, 0.4)

    spk_ribs = op_union(
        op_union(
            sd_round_box(x + 26.4, y + 24.0, z - 6.5, 0.8, 2.2, 6.0, 0.2),
            sd_round_box(x - 26.4, y + 24.0, z - 6.5, 0.8, 2.2, 6.0, 0.2),
        ),
        op_union(
            sd_round_box(x, y + 24.0, z - 32.9, 6.0, 2.2, 0.8, 0.2),
            sd_round_box(x, y + 24.0, z + 19.9, 6.0, 2.2, 0.8, 0.2),
        ),
    )
    spk_latches = op_union(
        op_union(
            sd_round_box(x + 26.2, y + 20.7, z - 6.5, 1.4, 1.2, 7.5, 0.3),
            sd_round_box(x - 26.2, y + 20.7, z - 6.5, 1.4, 1.2, 7.5, 0.3),
        ),
        op_union(
            sd_round_box(x, y + 20.7, z - 32.7, 7.5, 1.2, 1.4, 0.3),
            sd_round_box(x, y + 20.7, z + 19.7, 7.5, 1.2, 1.4, 0.3),
        ),
    )
    spk_assy = np.maximum(
        op_union(
            op_union(spk_collar, op_union(op_union(spk_standoff_l, spk_standoff_r), spk_standoff_t)),
            op_union(spk_ribs, spk_latches),
        ),
        torso_outer,
    )
    shell = op_union(shell, spk_assy)

    # 6. Open Torso Flank Cradles Flanking the Ø34 mm Round Speaker Magnet (Plugs Face Straight +Y Rear!)
    # 6a. Left-Upper Flank: MAX98357A Amp (18.0x19.5 mm PCB @ cx=-28.0, cy=-9.0, cz=+14.0; plugs -> y=+6.5 mm!)
    amp_wall_rail = op_subtract(
        sd_round_box(x + 36.8, y + 9.0, z - 14.0, 3.0, 3.0, 11.6, 0.4),
        sd_round_box(x + 34.5, y + 9.0, z - 14.0, 2.8, 1.4, 10.6, 0.2),
    )
    amp_bot_shelf = sd_round_box(x + 29.0, y + 9.0, z - 2.6, 10.5, 2.8, 1.1, 0.3)
    amp_top_latch = sd_round_box(x + 29.5, y + 9.0, z - 25.4, 9.0, 2.8, 1.0, 0.3)
    amp_crush = op_union(
        sd_round_box(x + 34.8, y + 7.9, z - 14.0, 1.2, 0.45, 7.0, 0.15),
        sd_round_box(x + 34.8, y + 10.1, z - 14.0, 1.2, 0.45, 7.0, 0.15),
    )
    amp_torso = np.maximum(
        op_union(op_union(amp_wall_rail, amp_crush), op_union(amp_bot_shelf, amp_top_latch)),
        torso_outer + 1.5,
    )

    # 6b. Left-Lower Flank: 6-Pin 3V3 Hub (cx=-28.0, cy=+6.5, cz=-1.0; plugs face +Y toward open rear bay)
    hub3_box = sd_round_box(x + 28.5, y - 6.5, z + 1.0, 9.5, 4.5, 5.4, 0.4)
    hub3_slot = sd_round_box(x + 27.5, y - 7.5, z + 1.0, 8.8, 4.8, 4.3, 0.2)
    hub3_ribs = op_union(
        sd_round_box(x + 28.0, y - 6.5, z + 4.3, 6.0, 2.2, 0.85, 0.15),
        sd_round_box(x + 28.0, y - 6.5, z - 2.3, 6.0, 2.2, 0.85, 0.15),
    )
    hub3_assy = np.maximum(op_union(op_subtract(hub3_box, hub3_slot), hub3_ribs), torso_outer + 1.5)

    # 6c. Right-Mid Flank: 6-Pin GND Hub (cx=+28.0, cy=+6.0, cz=+3.0; below SG90 bay, plugs face +Y rear)
    hubg_box = sd_round_box(x - 28.5, y - 6.0, z - 3.0, 9.5, 4.5, 5.4, 0.4)
    hubg_slot = sd_round_box(x - 27.5, y - 7.0, z - 3.0, 8.8, 4.8, 4.3, 0.2)
    hubg_ribs = op_union(
        sd_round_box(x - 28.0, y - 6.0, z + 0.3, 6.0, 2.2, 0.85, 0.15),
        sd_round_box(x - 28.0, y - 6.0, z - 6.3, 6.0, 2.2, 0.85, 0.15),
    )
    hubg_assy = np.maximum(op_union(op_subtract(hubg_box, hubg_slot), hubg_ribs), torso_outer + 1.5)

    # 6d. Right-Front Flank: Optional 1000uF Capacitor Upright C-Clip (cx=+27.0, cy=-10.5, cz=+3.0)
    cap_ring = op_subtract(
        op_subtract(
            sd_cylinder_z(x - 27.0, y + 10.5, z - 3.0, 7.2, 7.0),
            sd_cylinder_z(x - 27.0, y + 10.5, z - 3.0, 5.6, 8.0),
        ),
        sd_round_box(x - 22.0, y + 10.5, z - 3.0, 4.0, 3.8, 8.5, 0.2),
    )
    cap_strut = sd_round_box(x - 34.0, y + 10.5, z - 3.0, 4.5, 3.5, 6.5, 0.4)
    cap_torso = np.maximum(op_union(cap_ring, cap_strut), torso_outer + 1.5)

    # 6e. Upper Torso Wire-Guide C-Hooks right below neck spine (x = +/-19.5, y = +9.0, z = +35.0)
    torso_hook_l = op_subtract(
        sd_round_box(x + 21.0, y - 9.0, z - 35.0, 4.5, 2.5, 3.8, 0.4),
        sd_round_box(x + 19.5, y - 9.0, z - 34.5, 2.5, 3.5, 2.2, 0.3),
    )
    torso_hook_r = op_subtract(
        sd_round_box(x - 21.0, y - 9.0, z - 35.0, 4.5, 2.5, 3.8, 0.4),
        sd_round_box(x - 19.5, y - 9.0, z - 34.5, 2.5, 3.5, 2.2, 0.3),
    )
    torso_hooks = np.maximum(op_union(torso_hook_l, torso_hook_r), torso_outer + 1.5)

    shell = op_union(
        shell,
        op_union(
            op_union(amp_torso, op_union(hub3_assy, hubg_assy)),
            op_union(cap_torso, torso_hooks),
        ),
    )

    # Lower-rear LiPo Battery / 2-Foot Counterweight Tray (cx = 0.0, cy = +18.5, cz = -15.0)
    bat_outer = sd_round_box(x, y - 18.5, z + 15.0, 18.8, 6.6, 5.2, 0.5)
    bat_slot = sd_round_box(x, y - 18.5, z + 14.2, 17.2, 5.2, 5.5, 0.3)
    bat_ribs = op_union(
        sd_round_box(x - 10.0, y - 13.6, z + 14.5, 1.5, 0.55, 3.8, 0.15),
        sd_round_box(x + 10.0, y - 13.6, z + 14.5, 1.5, 0.55, 3.8, 0.15),
    )
    bat_tray = np.maximum(op_union(op_subtract(bat_outer, bat_slot), bat_ribs), torso_outer + 1.5)
    shell = op_union(shell, bat_tray)

    # 7. Smooth Oval Rear Service Opening (59.6 x 66.8 mm, r = 22.5 mm @ cz = +10.5)
    back_door_cut = sd_round_plate_y(x, y - 34.0, z - 10.5, 29.8, 14.0, 33.4, 22.5)
    shell = op_subtract(shell, back_door_cut)

    return shell


# ============================================================================
# Part 5: 3D Curved Oval Rear Access Hatch & ESP32-S3 Dock (`5_back_access_door.stl`)
#   - 61.0 x 68.0 mm smooth oval pill (r = 23.0 mm) matching `egg_torso_outer` rear curve
#   - Dedicated Vertical ESP32-S3 (28 x 62.5 mm) open-pin corner jaws (middle 36 mm open!)
#   - Gives +9.6 mm wire-bend gap behind speaker magnet + 2 internal wire-tie C-hooks
# ============================================================================


def sdf_back_door(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    # 1. Concentric 3D curved oval cap matching `egg_torso_outer` rear (-Z = outer surface, +Z = enters torso)
    door_map = egg_torso_outer(x, 42.5 - z, y + 10.5)
    curved_shell = np.maximum(door_map - 0.6, -(door_map + 1.8))
    oval_outer_mask = sd_round_plate_z(x, y, z - 6.5, 30.5, 34.0, 13.0, 23.0)
    plate = np.maximum(curved_shell, oval_outer_mask)

    # 2. Inner oval torso snap-lip (bx=29.2, by=32.8, r=22.2) w/ 31 mm vertical ESP32 channel + 6 perimeter barbs
    lip_channel = sd_round_box(x, y, z - 11.0, 15.5, 34.5, 7.0, 1.0)
    inner_lip = op_subtract(
        op_subtract(
            sd_round_plate_z(x, y, z - 11.0, 29.2, 32.8, 5.5, 22.2),
            sd_round_plate_z(x, y, z - 11.0, 27.6, 31.2, 6.5, 21.0),
        ),
        lip_channel,
    )
    barbs = op_union(
        op_union(
            sd_round_box(x + 29.4, y, z - 15.0, 0.8, 6.0, 0.8, 0.2),
            sd_round_box(x - 29.4, y, z - 15.0, 0.8, 6.0, 0.8, 0.2),
        ),
        op_union(
            op_union(
                sd_round_box(x + 18.0, y + 29.8, z - 15.0, 3.8, 0.8, 0.8, 0.2),
                sd_round_box(x - 18.0, y + 29.8, z - 15.0, 3.8, 0.8, 0.8, 0.2),
            ),
            op_union(
                sd_round_box(x + 18.0, y - 29.8, z - 15.0, 3.8, 0.8, 0.8, 0.2),
                sd_round_box(x - 18.0, y - 29.8, z - 15.0, 3.8, 0.8, 0.8, 0.2),
            ),
        ),
    )
    lip_and_barbs = np.maximum(op_union(inner_lip, barbs), door_map + 0.4)

    # 3. Vertical ESP32-S3 HW678 (28.0 x 62.5 mm) Open-Pin Corner Jaws (z_door = 7.5 -> y_torso = +35.0 mm!)
    esp_jaws = np.full_like(x, 1e6)
    for jy in (24.0, -23.5):  # Leaves middle 36 mm pin rows & copper 3.3V fix wire 100% open!
        jaw_l = op_subtract(
            sd_round_box(x + 15.8, y - jy, z - 6.3, 2.0, 3.5, 4.5, 0.3),
            sd_round_box(x + 14.4, y - jy, z - 7.5, 1.4, 4.0, 1.3, 0.2),
        )
        jaw_r = op_subtract(
            sd_round_box(x - 15.8, y - jy, z - 6.3, 2.0, 3.5, 4.5, 0.3),
            sd_round_box(x - 14.4, y - jy, z - 7.5, 1.4, 4.0, 1.3, 0.2),
        )
        rib_l = sd_round_box(x + 14.3, y - jy, z - 7.5, 0.65, 2.2, 0.65, 0.15)
        rib_r = sd_round_box(x - 14.3, y - jy, z - 7.5, 0.65, 2.2, 0.65, 0.15)
        pinch_l = sd_round_box(x + 14.7, y - jy, z - 6.3, 0.75, 2.2, 0.55, 0.15)
        pinch_r = sd_round_box(x - 14.7, y - jy, z - 6.3, 0.75, 2.2, 0.55, 0.15)
        esp_jaws = op_union(
            esp_jaws,
            op_union(op_union(jaw_l, jaw_r), op_union(op_union(rib_l, rib_r), op_union(pinch_l, pinch_r))),
        )
    esp_stop = sd_round_box(x, y + 32.2, z - 6.7, 10.0, 0.9, 3.5, 0.3)

    # 4. Internal Wire-Tie C-Hooks (x = +/-20.0, y = 0.0) to anchor bundled Dupont slack to the door
    door_hook_l = op_subtract(
        sd_round_box(x + 20.5, y, z - 7.5, 2.2, 4.0, 3.6, 0.4),
        sd_round_box(x + 19.8, y, z - 8.2, 2.8, 2.2, 2.0, 0.3),
    )
    door_hook_r = op_subtract(
        sd_round_box(x - 20.5, y, z - 7.5, 2.2, 4.0, 3.6, 0.4),
        sd_round_box(x - 19.8, y, z - 8.2, 2.8, 2.2, 2.0, 0.3),
    )
    door_hooks = op_union(door_hook_l, door_hook_r)

    mounts = np.maximum(op_union(op_union(esp_jaws, esp_stop), door_hooks), door_map + 0.8)
    door = op_union(op_union(plate, lip_and_barbs), mounts)

    # 5. Curved Acoustic Louver Slots & Off-Center Bottom USB-C Notch (x = +6.0)
    for sy in (-5.0, 0.0, 5.0):
        slot = sd_round_box(x, y - sy, z - 2.0, 11.0, 1.3, 6.0, 0.5)
        door = op_subtract(door, slot)
    usbc_notch = sd_round_box(x - 6.0, y + 32.5, z - 8.0, 5.8, 4.5, 6.0, 1.5)
    return op_subtract(door, usbc_notch)


# ============================================================================
# Part 6: Articulated Left & Right Movable Arms (`6_arms_left_and_right.stl`)
#   - Compliant split-slot shoulder peg + annular snap-lock bead
# ============================================================================


def sdf_single_arm(x: np.ndarray, y: np.ndarray, z: np.ndarray, side: float) -> np.ndarray:
    t = np.clip((18.5 - z) / 36.5, 0.0, 1.0)
    bow_x = -side * (2.6 * np.sin(t * math.pi) - 1.4 * (1.0 - t))
    r_profile = 6.7 + 1.15 * np.sin(t * math.pi) - 0.5 * t
    arm_tube = np.sqrt((x - bow_x) ** 2 + y ** 2) - r_profile
    arm_body = np.maximum(arm_tube, np.abs(z - 0.25) - 18.25)

    shoulder_dome = sd_sphere(x - bow_x, y, z - 14.0, 7.3)
    arm_body = smin(arm_body, shoulder_dome, 2.4)
    inner_bevel = (side * (x - side * 2.0) + (z - 16.5) * 0.58)
    arm_body = np.maximum(arm_body, inner_bevel)

    wrist_groove = op_subtract(
        sd_cylinder_z(x - bow_x, y, z + 16.5, 8.0, 0.45),
        sd_cylinder_z(x - bow_x, y, z + 16.5, 5.4, 1.0),
    )
    arm_body = op_subtract(arm_body, wrist_groove)

    hx = x + side * 0.4
    palm_outer = sd_ellipsoid(hx, y, z + 20.8, 6.1, 5.5, 5.0)
    palm_scoop = sd_sphere(hx - side * 2.5, y + 1.4, z + 22.0, 3.5)
    hand = op_subtract(palm_outer, palm_scoop)

    thumb = sd_ellipsoid(hx - side * 2.3, y + 3.1, z + 20.4, 2.1, 2.3, 3.1)
    hand = smin(hand, thumb, 1.0)

    fg1 = sd_round_box(hx + side * 1.5, y - 0.4, z + 23.6, 3.2, 0.4, 2.0, 0.2)
    fg2 = sd_round_box(hx + side * 1.5, y + 1.7, z + 23.6, 3.2, 0.4, 2.0, 0.2)
    hand = op_subtract(hand, op_union(fg1, fg2))

    arm_full = smin(arm_body, hand, 1.0)

    # Compliant snap-lock shoulder peg extending inward toward torso (+/-X) at z = +13.5
    peg_shaft = sd_cylinder_x(x - side * 5.2, y, z - 13.5, 2.6, 4.6)
    peg_bead = sd_cylinder_x(x - side * 8.6, y, z - 13.5, 2.95, 1.0)
    peg_slot = sd_round_box(x - side * 7.8, y, z - 13.5, 2.8, 3.2, 0.6, 0.15)
    peg = op_subtract(op_union(peg_shaft, peg_bead), peg_slot)
    return op_union(arm_full, peg)


def sdf_arms_pair(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    left_arm = sdf_single_arm(x + 15.5, y, z, -1.0)
    right_arm = sdf_single_arm(x - 15.5, y, z, +1.0)
    return op_union(left_arm, right_arm)


# ============================================================================
# Part 7: Movable Left & Right Thighs (`7_thighs_left_and_right.stl`)
#   - Compliant C-clip hip bore + split-slot ankle snap ball stud
# ============================================================================


def sdf_single_thigh(x: np.ndarray, y: np.ndarray, z: np.ndarray, side: float = 1.0) -> np.ndarray:
    r_leg = 10.6 - 0.04 * (10.2 - z)
    leg_cyl = np.sqrt(x * x + (y / 1.06) ** 2) - r_leg
    top_saddle = (z - 10.0 + side * x * 0.26)
    bot_cuff = -(z + 9.8)
    thigh_outer = np.maximum(leg_cyl, np.maximum(top_saddle, bot_cuff))

    hip_turret = sd_cylinder_x(x, y, z - 13.0, 7.8, 6.2)
    thigh_body = smin(thigh_outer, hip_turret, 1.6)

    hip_bore = sd_cylinder_x(x, y, z - 13.0, 3.6, 7.5)
    snap_throat = sd_round_box(x, y, z - 17.5, 7.0, 2.85, 4.0, 0.4)
    detent_groove_v = sd_round_box(x, y, z - 16.4, 7.0, 0.95, 1.1, 0.25)
    detent_groove_b = sd_round_box(x, y - 3.6, z - 13.0, 7.0, 1.1, 0.95, 0.25)
    detent_groove_h = sd_round_box(x, y + 3.6, z - 13.0, 7.0, 1.1, 0.95, 0.25)
    thigh_hollow = sd_cylinder_z(x, y, z + 0.8, 6.2, 6.5)
    thigh_body = op_subtract(
        thigh_body,
        op_union(
            op_union(
                op_union(hip_bore, snap_throat),
                op_union(detent_groove_v, op_union(detent_groove_b, detent_groove_h)),
            ),
            thigh_hollow,
        ),
    )

    ankle_neck = sd_cylinder_z(x, y, z + 10.6, 5.0, 2.0)
    ankle_ball = sd_sphere(x, y, z + 13.0, 5.85)
    ankle_key = sd_round_box(x, y, z + 13.0, 6.15, 1.05, 1.6, 0.3)
    ankle_split = sd_round_box(x, y, z + 15.6, 0.65, 6.5, 3.6, 0.15)
    ankle_assy = op_subtract(op_union(ankle_neck, op_union(ankle_ball, ankle_key)), ankle_split)
    return op_union(thigh_body, ankle_assy)


def sdf_thighs_pair(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    left_thigh = sdf_single_thigh(x + 15.5, y, z, -1.0)
    right_thigh = sdf_single_thigh(x - 15.5, y, z, +1.0)
    return op_union(left_thigh, right_thigh)


# ============================================================================
# Part 8: Movable Left & Right Shoes/Feet (`8_feet_left_and_right.stl`)
#   - Domed sneaker-pod shoes + extended anti-tip flat soles + zero-pitch ankle lock
# ============================================================================


def sdf_single_foot(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    toe_dome = sd_ellipsoid(x, y + 4.6, z + 3.1, 12.5, 18.2, 9.0)
    heel_dome = sd_ellipsoid(x, y - 2.5, z + 2.2, 12.0, 13.6, 9.2)
    shoe_pod = smin(toe_dome, heel_dome, 3.2)

    collar_outer = sd_cylinder_z(x, y - 0.8, z - 2.6, 11.2, 3.2)
    foot_outer = smin(shoe_pod, collar_outer, 2.0)

    collar_cup = sd_cylinder_z(x, y - 0.8, z - 5.2, 10.1, 2.5)
    foot_outer = op_subtract(foot_outer, collar_cup)

    ankle_ribs = op_union(
        op_union(
            sd_round_box(x, y - 0.8 + 9.9, z - 4.4, 2.2, 0.45, 1.3, 0.15),
            sd_round_box(x, y - 0.8 - 9.9, z - 4.4, 2.2, 0.45, 1.3, 0.15),
        ),
        op_union(
            sd_round_box(x + 9.9, y - 0.8, z - 4.4, 0.45, 2.2, 1.3, 0.15),
            sd_round_box(x - 9.9, y - 0.8, z - 4.4, 0.45, 2.2, 1.3, 0.15),
        ),
    )
    foot_outer = op_union(foot_outer, np.maximum(ankle_ribs, collar_outer))

    sole_clip = z + 6.5
    foot_outer = np.maximum(foot_outer, -sole_clip)

    socket_sphere = sd_sphere(x, y - 0.8, z - 1.2, 5.98)
    socket_throat = sd_cylinder_z(x, y - 0.8, z - 3.5, 5.45, 2.8)
    detent_slot_0 = sd_round_box(x, y - 0.8, z - 1.2, 6.55, 1.15, 2.0, 0.3)
    detent_slot_stride = sd_round_box(x, y - 0.8, z - 1.2, 1.15, 6.55, 2.0, 0.3)
    return op_subtract(
        foot_outer,
        op_union(op_union(socket_sphere, socket_throat), op_union(detent_slot_0, detent_slot_stride)),
    )


def sdf_feet_pair(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    left_foot = sdf_single_foot(x + 15.5, y, z)
    right_foot = sdf_single_foot(x - 15.5, y, z)
    return op_union(left_foot, right_foot)


# ============================================================================
# Binary STL Exporter & Mesh Statistics
# ============================================================================


def _tri_normals_and_area2(verts: np.ndarray, faces: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    tv = verts[faces]
    cross = np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0])
    area2 = np.linalg.norm(cross, axis=1)
    return cross / np.maximum(area2, 1e-12)[:, None], area2


def snap_vertices_to_sdf_surface(
    sdf_fn: Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray],
    verts: np.ndarray,
    faces: np.ndarray,
    step: float,
    iters: int = 2,
) -> np.ndarray:
    """Clamped Newton projection of marching-cubes vertices onto the exact SDF zero surface.

    Pulls vertices that marching cubes left on grid edges back onto the true surface (tighter rims
    and edges). Every vertex is clamped to <= 0.40 * step total displacement, and any move that
    flips or collapses an adjacent triangle is reverted, so thin crush ribs cannot fold over.
    """
    orig = verts.astype(np.float64)
    v = orig.copy()
    h = 0.25 * step
    max_move = 0.40 * step

    def f(p: np.ndarray) -> np.ndarray:
        return np.asarray(sdf_fn(p[:, 0], p[:, 1], p[:, 2]), dtype=np.float64)

    for _ in range(iters):
        d = f(v)
        grad = np.empty_like(v)
        for axis in range(3):
            off = np.zeros(3)
            off[axis] = h
            grad[:, axis] = (f(v + off) - f(v - off)) / (2.0 * h)
        g2 = np.sum(grad * grad, axis=1)
        ok = (g2 > 0.25) & np.isfinite(d)  # skip ill-conditioned gradients (crease saddles)
        v[ok] -= (d[ok] / g2[ok])[:, None] * grad[ok]
        disp = v - orig
        dn = np.linalg.norm(disp, axis=1)
        scale = np.minimum(1.0, max_move / np.maximum(dn, 1e-12))
        v = orig + disp * scale[:, None]

    n_old, a_old = _tri_normals_and_area2(orig, faces)
    for _ in range(8):
        n_new, a_new = _tri_normals_and_area2(v, faces)
        bad = (np.sum(n_old * n_new, axis=1) <= 0.2) | (a_new < np.maximum(1e-5, 0.10 * a_old))
        if not bad.any():
            break
        idx = np.unique(faces[bad].ravel())
        v[idx] = orig[idx]

    # Revert snapped vertices that landed on (or within 1e-4 mm of) another vertex: on thin walls the
    # projection can pull two distinct vertices to one point, which slicers weld into pinched edges.
    moved = np.any(v != orig, axis=1)
    for tol in (2e-4, 1e-5):
        for _ in range(8):
            keys = np.round(v.astype(np.float32) / tol).astype(np.int64)
            _, inv, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
            inv = inv.ravel()
            clash = (counts[inv] > 1) & moved
            if not clash.any():
                break
            v[clash] = orig[clash]
            moved &= ~clash
    return v.astype(np.float32)


def build_mesh_from_sdf(
    sdf_fn: Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray],
    bounds: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]],
    step: float = 0.85,
    snap: bool = False,
) -> Tuple[np.ndarray, np.ndarray]:
    (xmin, xmax), (ymin, ymax), (zmin, zmax) = bounds
    xs = np.arange(xmin, xmax + step, step, dtype=np.float32)
    ys = np.arange(ymin, ymax + step, step, dtype=np.float32)
    zs = np.arange(zmin, zmax + step, step, dtype=np.float32)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    vol = sdf_fn(X, Y, Z)
    del X, Y, Z
    # Keep grid samples at least 2e-3 mm (2 microns) away from 0.0 so marching-cubes edge interpolants
    # never land on grid corners (eliminates 100% of zero-area triangles and corner-welded pinches).
    eps = np.float32(2e-3)
    vol = np.where(np.abs(vol) < eps, np.where(vol >= 0.0, eps, -eps), vol)
    verts, faces, _, _ = marching_cubes(vol, level=0.0, spacing=(step, step, step))
    del vol
    verts[:, 0] += xmin
    verts[:, 1] += ymin
    verts[:, 2] += zmin
    orig_verts = verts.astype(np.float32).copy()
    if snap:
        verts = snap_vertices_to_sdf_surface(sdf_fn, verts, faces, step)
    # Shift Z so minimum Z sits at Z = 0.0 on the 3D print bed
    z_floor = verts[:, 2].min()
    verts[:, 2] -= z_floor
    orig_verts[:, 2] -= z_floor
    if snap:
        moved = np.any(verts != orig_verts, axis=1)
        for _ in range(6):
            keys = np.round(verts.astype(np.float64), 5)
            _, inv, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
            clash = (counts[inv.ravel()] > 1) & moved
            if not clash.any():
                break
            verts[clash] = orig_verts[clash]
            moved &= ~clash
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
    print("Generating 9 watertight hardware-fitted 3D-printable .STL files for RoboAssistant (Snap-Lock Kit)...")
    parts_spec = [
        (
            "1_head_shell.stl",
            "Pure Egg Head (~89x62x70 mm | Oval Visor, Left-Ear INMP441 Bay, GMT130, PTT, Wire Hooks)",
            sdf_head_shell,
            ((-48.0, 48.0), (-38.0, 38.0), (-38.0, 39.0)),
            0.60,
            (236, 240, 245),
        ),
        (
            "2_front_visor_bezel.stl",
            "Seamless 3D Curved Oval Visor Bezel (60x41 mm + 4-Barb Snap Fit)",
            sdf_front_visor,
            ((-33.0, 33.0), (-23.0, 23.0), (-3.5, 14.5)),
            0.50,
            (58, 66, 80),
        ),
        (
            "3_top_ptt_button_cap.stl",
            "Top PTT Button Cap (4-Petal Slotted Retention Flange + Wide Actuator)",
            sdf_top_button_cap,
            ((-14.0, 14.0), (-14.0, 14.0), (-6.5, 5.5)),
            0.45,
            (145, 155, 168),
        ),
        (
            "4_torso_shell.stl",
            "Pure Teardrop Egg Belly (~83x91x70 mm | Forward SUNVOICE, Chest Grille, Side Amp/Hubs, SG90, LiPo Bay)",
            sdf_torso_shell,
            ((-46.0, 46.0), (-53.0, 44.0), (-24.0, 50.0)),
            0.60,
            (236, 240, 245),
        ),
        (
            "5_back_access_door.stl",
            "Flush 3D Curved Oval ESP32-S3 Rear Dock (Open Middle Pins + USB-C Notch + 6-Barb Lock)",
            sdf_back_door,
            ((-33.0, 33.0), (-36.0, 36.0), (-1.5, 25.0)),
            0.50,
            (215, 222, 230),
        ),
        (
            "6_arms_left_and_right.stl",
            "Long Plump Curved Arms (Compliant Split-Slot Shoulder Pegs + Snap Beads)",
            sdf_arms_pair,
            ((-30.0, 30.0), (-12.0, 12.0), (-28.0, 24.0)),
            0.55,
            (232, 236, 242),
        ),
        (
            "7_thighs_left_and_right.stl",
            "Movable Hip-Pivot Thighs (Dual Hip Detents + Split-Slot Ankle Ball)",
            sdf_thighs_pair,
            ((-30.0, 30.0), (-14.0, 14.0), (-21.0, 22.0)),
            0.55,
            (232, 236, 242),
        ),
        (
            "8_feet_left_and_right.stl",
            "Movable Anti-Tip Shoes (Extended Flat Sole + Zero-Pitch Ankle Lock)",
            sdf_feet_pair,
            ((-31.0, 31.0), (-25.5, 19.0), (-8.0, 8.0)),
            0.55,
            (236, 240, 245),
        ),
        (
            "9_head_back_cap.stl",
            "Flush 3D Curved Rear Head Wiring Cover (Ø40.8 mm Snap-In Cap for Coiled Head Wires)",
            sdf_head_back_cap,
            ((-23.0, 23.0), (-23.0, 23.0), (-2.0, 14.0)),
            0.45,
            (226, 232, 238),
        ),
    ]

    built_parts = {}
    for part_idx, (fname, desc, fn, bounds, step, color) in enumerate(parts_spec, start=1):
        # Option A edge sharpening: 0.35 mm grid (finer than a 0.40 mm nozzle) + clamped SDF surface snap
        verts, faces = build_mesh_from_sdf(fn, bounds, step=min(step, PRINT_MESH_STEP_MM), snap=True)
        stl_path = STL_DIR / fname
        write_binary_stl(stl_path, verts, faces, f"Toy Part {part_idx} - {fname} (units=mm, 1:1 scale)")
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
    door_v, door_f, _, _, _ = built_parts["5_back_access_door.stl"]
    head_v, head_f, _, _, _ = built_parts["1_head_shell.stl"]
    visor_v, visor_f, _, _, _ = built_parts["2_front_visor_bezel.stl"]
    btn_v, btn_f, _, _, _ = built_parts["3_top_ptt_button_cap.stl"]
    cap_v, cap_f, _, _, _ = built_parts["9_head_back_cap.stl"]

    # Feet sit on ground z = 0..12.7 mm (ankle socket center at Z = +7.7 mm)
    feet_world = feet_v.copy()
    # Thighs ankle ball sits at Z = +7.7 mm, hip bore center at Z = +33.7 mm
    thighs_world = thighs_v.copy()
    thighs_world[:, 2] += 1.9
    # Torso bottom at Z = +25.7 mm, hip axle at +33.7 mm, z_local=0 at +47.2 mm, top deck at +91.2 mm
    torso_world = torso_v.copy()
    torso_world[:, 2] += 25.7

    # Flush 3D Curved Oval Rear Service Dock (locked to exact un-cut egg_torso_outer +0.6 mm rear contour)
    ys_probe = np.linspace(30.0, 50.0, 2001)
    d_probe = egg_torso_outer(np.zeros_like(ys_probe), ys_probe, np.full_like(ys_probe, 10.5))
    y_rear_outer = float(ys_probe[np.argmin(np.abs(d_probe - 0.6))])
    back_world = np.zeros_like(door_v)
    back_world[:, 0] = door_v[:, 0]
    back_world[:, 1] = -door_v[:, 2] + y_rear_outer
    back_world[:, 2] = door_v[:, 1] + 57.7

    # Head neck collar bottom at Z = +89.2 mm -> head center z_local=0 at +124.4 mm, top ring at +159.6 mm
    head_world = head_v.copy()
    head_world[:, 2] += 89.2

    # Flush 3D Curved Rear Head Cover (locked to un-cut egg_head_outer +0.6 mm rear contour)
    yh_probe = np.linspace(25.0, 42.0, 1701)
    dh_probe = egg_head_outer(np.zeros_like(yh_probe), yh_probe, np.zeros_like(yh_probe))
    y_head_rear = float(yh_probe[np.argmin(np.abs(dh_probe - 0.6))])
    cap_world = np.zeros_like(cap_v)
    cap_world[:, 0] = cap_v[:, 0]
    cap_world[:, 1] = -cap_v[:, 2] + y_head_rear
    cap_world[:, 2] = cap_v[:, 1] + 124.4

    # 3D curved oval visor bezel (60x41 mm) snaps flush over front egg dome centered at Z = 124.4 mm
    visor_world = np.zeros_like(visor_v)
    visor_world[:, 0] = visor_v[:, 0]
    visor_world[:, 1] = visor_v[:, 2] + head_world[:, 1].min() - 0.8
    visor_world[:, 2] = -visor_v[:, 1] + 124.4

    # Centered proud silver top PTT button cap + cyan halo ring at x = 0.0, y = +4.5
    btn_world = btn_v.copy()
    btn_world[:, 1] += 4.5
    btn_world[:, 2] += 163.5 - btn_world[:, 2].max()

    halo_v, halo_f = build_mesh_from_sdf(
        lambda x, y, z: op_subtract(
            sd_cylinder_z(x, y, z, 12.8, 0.8),
            sd_cylinder_z(x, y, z, 11.2, 1.6),
        ),
        ((-15.0, 15.0), (-15.0, 15.0), (-2.0, 2.0)),
        step=0.5,
    )
    halo_v[:, 1] += 4.5
    halo_v[:, 2] += 159.8

    arm_l_v = sdf_single_arm_mesh(-1.0)
    arm_r_v = sdf_single_arm_mesh(+1.0)

    total_height_mm = max(v[:, 2].max() for v in (feet_world, thighs_world, torso_world, head_world, btn_world))
    print(f"  [OK] Total Assembled Standing Height: {total_height_mm:.1f} mm ({total_height_mm / 10.0:.2f} cm / {total_height_mm / 25.4:.2f} inches)")

    # Glossy dark obsidian 3D curved oval OLED panel + curved expressive glowing cyan eyes, arched brows & smile
    def sdf_curved_oled(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
        shell_d = egg_head_outer(x, z - 35.5, -y)
        slab = np.maximum(shell_d - 0.2, -(shell_d + 1.8))
        oval = sd_round_plate_z(x, y, z - 4.0, 27.5, 18.2, 10.0, 17.8)
        return np.maximum(slab, oval)

    scr_v, scr_f = build_mesh_from_sdf(
        sdf_curved_oled,
        ((-29.5, 29.5), (-20.5, 20.5), (-2.5, 14.0)),
        step=0.7,
    )
    scr_z0 = scr_v[:, 2] - scr_v[:, 2].min()
    scr_rot = np.zeros_like(scr_v)
    scr_rot[:, 0] = scr_v[:, 0]
    scr_rot[:, 1] = scr_z0 + head_world[:, 1].min() - 0.6
    scr_rot[:, 2] = -scr_v[:, 1] + 124.4

    def sdf_cute_face(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
        yc = y - (0.011 * x ** 2 + 0.009 * z ** 2)
        eye_l = op_subtract(
            sd_cylinder_y(x + 7.4, yc, z - 0.5, 5.8, 1.0),
            sd_cylinder_y(x + 7.4, yc, z - 0.5, 2.2, 2.0),
        )
        eye_r = op_subtract(
            sd_cylinder_y(x - 7.4, yc, z - 0.5, 5.8, 1.0),
            sd_cylinder_y(x - 7.4, yc, z - 0.5, 2.2, 2.0),
        )
        brow_l = np.maximum(
            op_subtract(
                sd_cylinder_y(x + 7.4, yc, z - 2.6, 7.5, 1.0),
                sd_cylinder_y(x + 7.4, yc, z - 2.6, 6.1, 2.0),
            ),
            -(z - 5.8),
        )
        brow_r = np.maximum(
            op_subtract(
                sd_cylinder_y(x - 7.4, yc, z - 2.6, 7.5, 1.0),
                sd_cylinder_y(x - 7.4, yc, z - 2.6, 6.1, 2.0),
            ),
            -(z - 5.8),
        )
        smile = np.maximum(
            op_subtract(
                sd_cylinder_y(x, yc, z + 3.6, 3.6, 1.0),
                sd_cylinder_y(x, yc, z + 3.6, 2.1, 2.0),
            ),
            z + 4.8,
        )
        return op_union(op_union(eye_l, eye_r), op_union(op_union(brow_l, brow_r), smile))

    eye_v, eye_f = build_mesh_from_sdf(
        sdf_cute_face,
        ((-18.0, 18.0), (-2.5, 5.5), (-12.0, 14.0)),
        step=0.45,
    )
    eye_v[:, 1] += head_world[:, 1].min() - 1.3
    eye_v[:, 2] += 124.4 - eye_v[:, 2].mean() + 0.6

    assembled_meshes = [
        (feet_world, feet_f, (238, 242, 247)),
        (thighs_world, thighs_f, (234, 238, 244)),
        (torso_world, torso_f, (238, 242, 247)),
        (back_world, door_f, (215, 222, 230)),
        (head_world, head_f, (238, 242, 247)),
        (cap_world, cap_f, (226, 232, 238)),
        (visor_world, visor_f, (22, 26, 34)),
        (scr_rot, scr_f, (16, 20, 28)),
        (eye_v, eye_f, (0, 240, 255)),
        (halo_v, halo_f, (0, 240, 255)),
        (btn_world, btn_f, (150, 160, 174)),
        (arm_l_v[0], arm_l_v[1], (234, 238, 244)),
        (arm_r_v[0], arm_r_v[1], (234, 238, 244)),
    ]

    hero_img = render_mesh_to_image(
        assembled_meshes,
        img_w=700,
        img_h=860,
        yaw_deg=-24.0,
        pitch_deg=12.0,
        scale=4.05,
        center_offset=(0.0, 0.0, 82.0),
    )

    sheet = Image.new("RGB", (1620, 860), (14, 18, 26))
    sheet.paste(hero_img, (0, 0))
    draw = ImageDraw.Draw(sheet)

    # Right-side 3x3 grid of all 9 individual printable .STL parts
    grid_positions = [
        ("1_head_shell.stl", 715, 35, 2.0, (0.0, 0.0, 34.0)),
        ("2_front_visor_bezel.stl", 1015, 35, 2.3, (0.0, 0.0, 6.0)),
        ("3_top_ptt_button_cap.stl", 1315, 35, 4.0, (0.0, 0.0, 3.0)),
        ("4_torso_shell.stl", 715, 301, 1.85, (0.0, 0.0, 34.0)),
        ("5_back_access_door.stl", 1015, 301, 2.2, (0.0, 0.0, 8.5)),
        ("9_head_back_cap.stl", 1315, 301, 3.0, (0.0, 0.0, 4.5)),
        ("6_arms_left_and_right.stl", 715, 567, 2.3, (0.0, 0.0, 23.0)),
        ("7_thighs_left_and_right.stl", 1015, 567, 2.6, (0.0, 0.0, 19.0)),
        ("8_feet_left_and_right.stl", 1315, 567, 2.4, (0.0, 0.0, 6.5)),
    ]

    for fname, gx, gy, gscale, goff in grid_positions:
        v, f, col, dims, _ = built_parts[fname]
        tile = render_mesh_to_image(
            [(v, f, col)],
            img_w=288,
            img_h=252,
            yaw_deg=-30.0,
            pitch_deg=24.0,
            scale=gscale,
            center_offset=goff,
        )
        sheet.paste(tile, (gx, gy))
        draw.rectangle([gx, gy, gx + 288, gy + 252], outline=(45, 65, 90), width=2)
        draw.text(
            (gx + 8, gy + 6),
            f"{fname} ({dims[0]:.0f}x{dims[1]:.0f}x{dims[2]:.0f} mm)",
            fill=(0, 225, 255),
        )

    draw.text(
        (24, 18),
        f"ROBOASSISTANT WIRE-MESH READY CAD ({total_height_mm:.0f} mm Standing | 9 Watertight Snap-Lock .STL Parts)",
        fill=(0, 225, 255),
    )
    draw.text(
        (24, 825),
        f"Exact Height: {total_height_mm:.1f} mm ({total_height_mm / 10.0:.2f} cm) | 20cm Wire-Mesh Routed + Roadmap SG90/LiPo + Anti-Tip Soles",
        fill=(170, 185, 205),
    )

    preview_path = OUT_DIR / "robot_cad_preview.png"
    sheet.save(preview_path)
    print(f"  [OK] Saved 3D CAD Engineering Preview Sheet -> {preview_path}")


def sdf_single_arm_mesh(side: float) -> Tuple[np.ndarray, np.ndarray]:
    def fn(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
        return sdf_single_arm(x, y, z, side)

    v, f = build_mesh_from_sdf(fn, ((-13.0, 13.0), (-11.0, 11.0), (-28.0, 24.0)), step=0.60)
    v[:, 0] += -side * 43.2
    v[:, 2] += 32.9
    return v, f


if __name__ == "__main__":
    main()
