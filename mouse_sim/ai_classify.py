"""AI-assisted component-type classification via the OpenRouter API.

This module is the vision-first classifier core.  For each part it computes a
deterministic descriptor vector (geometry metrics + normalized name), renders
orthographic thumbnails with a stdlib-only z-buffer rasterizer, and asks a
vision model (OpenRouter ``chat/completions``) to assign a canonical component
type from :data:`classification.CANONICAL_COMPONENT_TYPES`.  Results are fused
with the deterministic rule classifier by :func:`merge_classification` and
cached on disk keyed by a part-content hash, so re-runs are free and stable.

Privacy: only the name, the descriptor vector, and the rendered thumbnail
leave the machine.  Full meshes are never sent.

The module is deliberately dependency-free (stdlib ``urllib``, ``zlib``,
``struct``) to match the rest of ``mouse_sim``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .classification import CANONICAL_COMPONENT_TYPES, canonical_component_type

# Endpoints that recently refused a connection are remembered (process-local)
# so subsequent analyses skip the doomed retry loop entirely instead of
# stalling every drop test for seconds.  Entries expire after this many
# seconds so a recovered local model is picked up again.
_ENDPOINT_DOWN_CACHE: Dict[str, float] = {}
_ENDPOINT_DOWN_LOCK = threading.Lock()
_ENDPOINT_DOWN_TTL_S = 60.0

# ---------------------------------------------------------------------------
# Constants & environment configuration
# ---------------------------------------------------------------------------

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "xiaomi/mimo-v2.5"
DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"
DEFAULT_PROVIDER = "Xiaomi"

#: Estimated USD per 1M input / 1M output tokens for known models (kept in a
#: small table so the UI can estimate cost before running).
MODEL_PRICES_USD_PER_1M = {
    "xiaomi/mimo-v2.5": (0.10, 0.40),
    "openai/gpt-5.6-luna-pro": (0.15, 0.60),
    "openai/gpt-5.6-luna": (0.15, 0.60),
    "google/gemini-3.7-flash": (0.10, 0.40),
    "x-ai/grok-4.6": (0.20, 0.80),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
    "anthropic/claude-3.5-sonnet": (3.00, 15.00),
    "google/gemini-flash-1.5": (0.075, 0.30),
    "meta-llama/llama-3.2-11b-vision-instruct": (0.10, 0.30),
}

MAX_BATCH_PARTS = 8
DEFAULT_TIMEOUT_S = 45.0
DEFAULT_RETRIES = 2
DEFAULT_MAX_PARTS = 64
DEFAULT_CACHE_CAPACITY = 500
THUMBNAIL_SIZE = 192
THUMBNAIL_VIEWS = 3  # top, front, side

PROMPT_VERSION = "ai-classify-v1"


def _load_dotenv() -> None:
    """Load key-value pairs from .env into os.environ if present.

    Runs ONCE, lazily, on the first configuration read (``api_key`` /
    ``is_enabled`` / ``model_name`` ...).  The load is intentionally NOT a
    module-import side effect: a lazy import inside a worker thread would
    otherwise re-populate ``os.environ`` from the repository ``.env`` AFTER
    a caller had explicitly unset or overridden the AI configuration,
    silently re-enabling the network path (the ``/api/classify`` heuristic
    fallback hung on a real HTTPS call for exactly this reason).
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for env_path in candidates:
        if env_path.is_file():
            try:
                with env_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, _, v = line.partition("=")
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
            except OSError:
                pass
            break


_DOTENV_LOADED = False


def _env(name, default=None):
    # Lazy one-time .env load: configuration is read through this helper, so
    # the first read triggers the load.  A caller that already set (or
    # explicitly unset) the variable before the first read wins — the load
    # only fills keys that are still absent.
    _load_dotenv()
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(name, default):
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def api_key() -> Optional[str]:
    return _env("OPENROUTER_API_KEY")


def is_enabled() -> bool:
    """AI classification is enabled only when a key is present AND the feature
    flag is on (or the caller is the explicit web review path)."""
    return bool(api_key()) and _env("MOUSE_SIM_AI_ENABLED", "0") in ("1", "true", "yes")


def model_name() -> str:
    return _env("MOUSE_SIM_AI_MODEL", DEFAULT_MODEL)


def embedding_model_name() -> str:
    return _env("MOUSE_SIM_AI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


def provider_name() -> Optional[str]:
    return _env("MOUSE_SIM_AI_PROVIDER", DEFAULT_PROVIDER)


def model_prices_usd() -> Tuple[float, float]:
    return MODEL_PRICES_USD_PER_1M.get(model_name(), (0.15, 0.60))


def max_parts_limit() -> int:
    return _env_int("MOUSE_SIM_AI_MAX_PARTS", DEFAULT_MAX_PARTS)


def cache_dir() -> Path:
    return Path(_env("MOUSE_SIM_AI_CACHE_DIR", str(Path.cwd() / ".web-cache" / "ai_classify")))


def cache_capacity() -> int:
    return _env_int("MOUSE_SIM_AI_CACHE_CAPACITY", DEFAULT_CACHE_CAPACITY)


# ---------------------------------------------------------------------------
# Part descriptors (deterministic geometry + name signal)
# ---------------------------------------------------------------------------


def normalize_part_name(name: Optional[str]) -> str:
    """Normalize a CAD part name for the classifier.

    Strips assembly suffixes (``_2``, ``_1_1_1_1``, ``_ASM``), parenthesized
    junk, and keeps NFKC-folded alphanumeric tokens of length >= 2.  The
    original string is preserved by the caller when needed.
    """
    if name is None:
        return ""
    text = name.replace("\\", "/").rsplit("/", 1)[-1]
    text = text.strip().casefold()
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    # Drop trailing numeric suffixes and ASM markers.
    while True:
        base, sep, suffix = text.rpartition("_")
        if sep and (suffix.isdigit() or suffix in ("asm", "asms")):
            text = base
            continue
        break
    # Drop a single leading CAD prefix letter token (e.g. "C-WHEEL" → "WHEEL").
    parts_tokens = text.split("_")
    if len(parts_tokens) > 1 and len(parts_tokens[0]) == 1:
        parts_tokens = parts_tokens[1:]
    # Keep single-letter tokens in the middle (product codes like "TOP-C").
    return "_".join(parts_tokens)


def rule_classify_name(name: Optional[str]) -> Optional[Dict[str, Any]]:
    """Deterministic rule classifier matching CAD names, fasteners, battery pouch numbers, and PCB tokens."""
    if not name:
        return None
    raw = str(name).strip()
    norm = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_")

    # 1. Screws & Fasteners (e.g. 2X4, 2X5, 2X3, 2X4_4, 2X5_2, M2X4, PM2*4, TYPE-C_L_1)
    if re.search(r"^(?:m?\d+(?:\.\d+)?_?[x*]_?\d+(?:\.\d+)?|\d+[x*]\d+)(?:_\d+)*$", norm) or any(k in norm for k in ("screw", "fastener", "bolt", "torx", "philips", "thread", "insert", "screw_boss", "type_c_l")):
        return {
            "component_type": "screw_boss",
            "confidence": 0.95,
            "reasons": [f"Name matches standard fastener / screw specification ({raw})"],
        }

    # 2. Batteries (e.g. 602024-300AH, 502030, lipo, mah, ah, battery)
    if re.search(r"\b\d{4,6}[-_]?\d*a?h?\b", norm) or any(k in norm for k in ("battery", "lipo", "polymer", "cell", "mah", "300ah", "400ah", "500ah", "602024")):
        return {
            "component_type": "battery",
            "confidence": 0.95,
            "reasons": [f"Name matches rechargeable battery / pouch cell nomenclature ({raw})"],
        }

    # 3. PCBs & Electronics (e.g. G2-PCB2, DM103-PCBA, MAIN_PCB)
    if any(k in norm for k in ("pcb", "pcba", "board", "mainboard", "motherboard", "fpc", "circuit", "pwa", "type_c", "usb", "connector", "jack")):
        return {
            "component_type": "pcb",
            "confidence": 0.95,
            "reasons": [f"Name identifies printed circuit board / electronic interface ({raw})"],
        }

    # 4. Sensor & Optics (e.g. SENSOR_PACKAGE_SANITIZED, LENS_OPTICAL, TOUJING-X)
    if any(k in norm for k in ("sensor", "lens", "prism", "optical", "paw", "pmw", "pixart", "optics", "sensor_package")):
        return {
            "component_type": "sensor",
            "confidence": 0.95,
            "reasons": [f"Name identifies optical sensor or lens component ({raw})"],
        }

    # 5. Rotary Encoder / Wheel Module (e.g. ENCODER-11, SCROLL_ENCODER)
    if any(k in norm for k in ("encoder", "rotary_encoder", "wheel_encoder")):
        return {
            "component_type": "encoder",
            "confidence": 0.95,
            "reasons": [f"Name matches rotary encoder / wheel module ({raw})"],
        }

    # 6. Scroll Wheel & Wheel Subcomponents (e.g. C-WHEEL-01FK, CW-XWD, TD011-TZ)
    if any(k in norm for k in ("wheel", "scroll", "roller", "wheel_assembly", "c_wheel", "cw_xwd", "td011_tz")):
        return {
            "component_type": "scroll_wheel",
            "confidence": 0.95,
            "reasons": [f"Name matches scroll wheel assembly ({raw})"],
        }

    # 7. Buttons & Switches (e.g. SWITCH-12858735, SWITCH-12858735_1)
    if any(k in norm for k in ("switch", "microswitch", "d2fc", "omron", "kailh", "huano", "ttc")):
        return {
            "component_type": "main_button",
            "confidence": 0.95,
            "reasons": [f"Name indicates click button / microswitch ({raw})"],
        }
    if any(k in norm for k in ("button", "click", "lmb", "rmb", "left_btn", "right_btn", "paddle")):
        return {
            "component_type": "main_button",
            "confidence": 0.90,
            "reasons": [f"Name indicates click button paddle ({raw})"],
        }
    if any(k in norm for k in ("side", "thumb", "xwd", "cw_")):
        return {
            "component_type": "side_button",
            "confidence": 0.90,
            "reasons": [f"Name indicates side thumb button / microswitch ({raw})"],
        }

    # 8. Shells (e.g. TD011-TOP-C, TD011-BOT1)
    if any(k in norm for k in ("top", "upper", "palm", "roof", "top_cover", "shell_top", "top_c", "td011_top")):
        return {
            "component_type": "top_shell",
            "confidence": 0.95,
            "reasons": [f"Name matches top palm housing ({raw})"],
        }
    if any(k in norm for k in ("bot", "bottom", "base", "lower", "floor", "chassis", "shell_bottom", "bot1", "td011_bot")):
        return {
            "component_type": "bottom_shell",
            "confidence": 0.95,
            "reasons": [f"Name matches bottom base plate ({raw})"],
        }

    # 9. Skates / Foot pads (e.g. EVA-TP, EVA-TP_4, PTFE)
    if any(k in norm for k in ("skate", "feet", "foot", "glide", "pad", "tz", "eva", "tp", "ptfe", "teflon", "footpad")):
        return {
            "component_type": "foot_pad",
            "confidence": 0.95,
            "reasons": [f"Name matches low-friction mouse foot / skate ({raw})"],
        }

    # 10. Internal Chassis, Brackets, Light Guides, Subframes (e.g. TD011-CE, C-PQ-2_7, PRT0012, DCJ-01, TOUJING-X, BNKG-02, MANIFOLD)
    if any(k in norm for k in ("frame", "skeleton", "bracket", "carrier", "holder", "clip", "tg", "ce", "td011_ce", "c_pq", "prt0012", "prt", "dcj", "toujing", "bnkg", "manifold", "brep", "structure", "internal", "diffuser", "light_guide", "subframe")):
        return {
            "component_type": "internal_structure",
            "confidence": 0.90,
            "reasons": [f"Name matches internal chassis / bracket structure ({raw})"],
        }

    return None


def rule_classify_geometry(
    desc: Mapping[str, Any],
    assembly_bounds: Optional[Tuple[Sequence[float], Sequence[float]]] = None,
    name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Geometric rule classification for unnamed or anonymous mouse CAD bodies."""
    size_m = desc.get("size_m") or [0.0, 0.0, 0.0]
    dx, dy, dz = size_m
    max_dim = max(dx, dy, dz)
    vol = desc.get("volume_m3") or (dx * dy * dz)
    bounds = desc.get("bounds_m") or [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    # Fastener / Screw pillar: max dimension <= 12mm and tiny volume
    if max_dim <= 0.012 and (vol < 3e-7 or (dx < 0.006 and dy < 0.006 and dz < 0.012)):
        return {
            "component_type": "screw_boss",
            "confidence": 0.90,
            "reasons": [f"Geometry matches small fastener / screw pillar (L={max_dim*1000:.1f}mm)"],
        }

    # Relative spatial positions in mouse assembly
    if assembly_bounds and len(assembly_bounds[0]) >= 3 and len(assembly_bounds[1]) >= 3:
        amin, amax = assembly_bounds
        z_span = max(1e-6, amax[2] - amin[2])
        y_span = max(1e-6, amax[1] - amin[1])
        x_span = max(1e-6, amax[0] - amin[0])
        part_cz = ((bounds[2] + bounds[5]) / 2.0 - amin[2]) / z_span
        part_zmin = (bounds[2] - amin[2]) / z_span
        part_zmax = (bounds[5] - amin[2]) / z_span
        part_cy = ((bounds[1] + bounds[4]) / 2.0 - amin[1]) / y_span
        part_cx = ((bounds[0] + bounds[3]) / 2.0 - (amin[0] + amax[0]) / 2.0)

        # Foot pad / Skate: at bottom floor, ultra-thin
        if part_zmin <= 0.05 and dz <= 0.002 and (dx > 0.005 or dy > 0.005):
            return {
                "component_type": "foot_pad",
                "confidence": 0.90,
                "reasons": [f"Flat thin glide plate at bottom floor (Z_min={bounds[2]*1000:.1f}mm, dz={dz*1000:.2f}mm)"],
            }

        # Top Shell: high elevation, large XY footprint
        if part_zmax >= 0.70 and dx >= 0.35 * x_span and dy >= 0.40 * y_span:
            return {
                "component_type": "top_shell",
                "confidence": 0.90,
                "reasons": ["Large curved upper cover at top assembly elevation"],
            }

        # Bottom Shell: bottom elevation, large XY footprint
        if part_zmin <= 0.15 and dx >= 0.35 * x_span and dy >= 0.40 * y_span:
            return {
                "component_type": "bottom_shell",
                "confidence": 0.90,
                "reasons": ["Large underside base tray at bottom assembly elevation"],
            }

        # Scroll Wheel: front-center position, cylindrical/wheel aspect ratio
        if part_cy >= 0.50 and abs(part_cx) <= 0.010 and 0.015 <= max(dy, dz) <= 0.035 and dx <= 0.012:
            return {
                "component_type": "scroll_wheel",
                "confidence": 0.90,
                "reasons": ["Front-center cylindrical wheel geometry"],
            }

        # Main Button click paddle: top front left or right
        if part_zmax >= 0.50 and part_cy >= 0.55 and dx >= 0.15 * x_span and dy >= 0.20 * y_span:
            return {
                "component_type": "main_button",
                "confidence": 0.85,
                "reasons": ["Forward upper click button paddle"],
            }

    # Any other solid body in mouse assembly is internal structure / chassis
    if max_dim > 0:
        return {
            "component_type": "internal_structure",
            "confidence": 0.90,
            "reasons": ["Internal chassis / frame / bracket structure from assembly topology"],
        }

    return None


def _triangle_areas(vertices: Sequence[Sequence[float]], triangles: Sequence[Sequence[int]]) -> List[float]:
    areas = []
    for tri in triangles:
        if len(tri) < 3:
            continue
        a = vertices[tri[0]]
        b = vertices[tri[1]]
        c = vertices[tri[2]]
        if not a or not b or not c:
            continue
        ux = b[0] - a[0]
        uy = b[1] - a[1]
        uz = b[2] - a[2]
        vx = c[0] - a[0]
        vy = c[1] - a[1]
        vz = c[2] - a[2]
        cx = uy * vz - uz * vy
        cy = uz * vx - ux * vz
        cz = ux * vy - uy * vx
        areas.append(0.5 * math.sqrt(cx * cx + cy * cy + cz * cz))
    return areas


def _projected_xy_area(vertices: Sequence[Sequence[float]], triangles: Sequence[Sequence[int]]) -> float:
    """Projected XY footprint area, counting each XY footprint region once."""
    if not vertices or not triangles:
        return 0.0
    bounds = _mesh_bounds(vertices)
    dx = max(0.0, bounds[1][0] - bounds[0][0])
    dy = max(0.0, bounds[1][1] - bounds[0][1])
    if dx <= 0 or dy <= 0:
        return 0.0
    grid_res = 64
    cell_w = dx / grid_res
    cell_h = dy / grid_res
    grid: Dict[Tuple[int, int], bool] = {}
    for tri in triangles:
        if len(tri) < 3:
            continue
        try:
            a = vertices[int(tri[0])]
            b = vertices[int(tri[1])]
            c = vertices[int(tri[2])]
        except (IndexError, TypeError, ValueError):
            continue
        if not a or not b or not c:
            continue
        xs = [a[0], b[0], c[0]]
        ys = [a[1], b[1], c[1]]
        min_gx = max(0, min(grid_res - 1, int(math.floor((min(xs) - bounds[0][0]) / cell_w))))
        max_gx = max(0, min(grid_res - 1, int(math.floor((max(xs) - bounds[0][0]) / cell_w))))
        min_gy = max(0, min(grid_res - 1, int(math.floor((min(ys) - bounds[0][1]) / cell_h))))
        max_gy = max(0, min(grid_res - 1, int(math.floor((max(ys) - bounds[0][1]) / cell_h))))
        for gx in range(min_gx, max_gx + 1):
            for gy in range(min_gy, max_gy + 1):
                px = bounds[0][0] + (gx + 0.5) * cell_w
                py = bounds[0][1] + (gy + 0.5) * cell_h
                if _point_in_triangle(px, py, xs, ys):
                    grid[(gx, gy)] = True
    return len(grid) * cell_w * cell_h


def _mirror_symmetry_x(vertices: Sequence[Sequence[float]], triangles: Sequence[Sequence[int]]) -> float:
    """Fraction of the mesh's area whose triangles have a mirror twin within
    a small tolerance — a cheap self-overlap symmetry score."""
    if not triangles or not vertices:
        return 0.0
    bounds = _mesh_bounds(vertices)
    dim = max(bounds[1][i] - bounds[0][i] for i in range(3))
    tol = max(1e-4, dim * 0.02) if dim > 0 else 1e-4

    step = max(1, len(triangles) // 100)
    sampled = triangles[::step]
    by_side_pos: List[Tuple[float, float, float]] = []
    by_side_neg: List[Tuple[float, float, float]] = []
    for tri in sampled:
        if len(tri) < 3:
            continue
        try:
            a = vertices[int(tri[0])]
            b = vertices[int(tri[1])]
            c = vertices[int(tri[2])]
        except (IndexError, TypeError, ValueError):
            continue
        if not a or not b or not c:
            continue
        cx = (a[0] + b[0] + c[0]) / 3.0
        cy = (a[1] + b[1] + c[1]) / 3.0
        cz = (a[2] + b[2] + c[2]) / 3.0
        if abs(cx) < tol:
            continue
        if cx > 0:
            by_side_pos.append((cx, cy, cz))
        else:
            by_side_neg.append((cx, cy, cz))
    if not by_side_pos or not by_side_neg:
        return 0.0
    matched = 0
    for cx, cy, cz in by_side_pos:
        for mx, my, mz in by_side_neg:
            if abs(cx + mx) < tol and abs(cy - my) < tol * 2 and abs(cz - mz) < tol * 2:
                matched += 1
                break
    return matched / len(by_side_pos)


def _sanitize_mesh(geometry: Mapping[str, Any]) -> Tuple[List[List[float]], List[List[int]]]:
    """Extract and sanitize vertex floats and integer triangle indices."""
    vertices: List[List[float]] = []
    triangles: List[List[int]] = []
    geom_type = str(geometry.get("type", "mesh"))
    if geom_type == "mesh":
        raw_vertices = geometry.get("vertices") or []
        raw_triangles = geometry.get("triangles") or []
        for entry in raw_vertices:
            if isinstance(entry, (list, tuple)) and len(entry) >= 3:
                vertices.append([float(entry[0]), float(entry[1]), float(entry[2])])
        for entry in raw_triangles:
            if isinstance(entry, (list, tuple)) and len(entry) >= 3:
                try:
                    triangles.append([int(entry[0]), int(entry[1]), int(entry[2])])
                except (TypeError, ValueError, IndexError):
                    continue
    if geom_type in ("box", "sphere", "cylinder", "cone", "frustum"):
        vertices, triangles = _primitive_mesh(geom_type, geometry)
    return vertices, triangles


def part_descriptors(geometry: Mapping[str, Any], name: Optional[str] = None) -> Dict[str, Any]:
    """Compute the deterministic descriptor vector for a geometry dict.

    Accepts the wire geometry shape (``{"type": "mesh", "vertices": [...],
    "triangles": [...]}`` or a primitive dict with ``size/radius/height``).
    All outputs are finite, JSON-serializable, NaN-guarded.
    """
    vertices, triangles = _sanitize_mesh(geometry)
    geom_type = str(geometry.get("type", "mesh"))

    bounds = _mesh_bounds(vertices)
    size = [bounds[1][i] - bounds[0][i] for i in range(3)]
    max_dim = max(size) if size else 0.0
    flatness = (min(size) / max_dim) if max_dim > 0 else 0.0
    areas = _triangle_areas(vertices, triangles)
    surface_area = sum(areas)
    projected = _projected_xy_area(vertices, triangles)
    footprint_coverage = projected / max(bounds[1][0] - bounds[0][0], 1e-12) / max(bounds[1][1] - bounds[0][1], 1e-12) if surface_area > 0 else 0.0
    centroid = _mesh_centroid(vertices, triangles)
    bbox_center = [(bounds[0][i] + bounds[1][i]) / 2.0 for i in range(3)]
    skew = [centroid[i] - bbox_center[i] for i in range(3)]
    return {
        "name_normalized": normalize_part_name(name),
        "name_raw": str(name or ""),
        "geometry_type": geom_type,
        "vertex_count": len(vertices),
        "triangle_count": len(triangles),
        "bounds_m": [round(bounds[0][0], 6), round(bounds[0][1], 6), round(bounds[0][2], 6),
                     round(bounds[1][0], 6), round(bounds[1][1], 6), round(bounds[1][2], 6)],
        "size_m": [round(size[0], 6), round(size[1], 6), round(size[2], 6)],
        "max_dim_m": round(max_dim, 6),
        "flatness": round(flatness, 4),
        "surface_area_m2": round(surface_area, 8),
        "volume_m3": round(_mesh_volume(vertices, triangles), 10),
        "footprint_coverage": round(footprint_coverage, 4),
        "centroid_offset_m": [round(skew[0], 6), round(skew[1], 6), round(skew[2], 6)],
        "mirror_symmetry_x": round(_mirror_symmetry_x(vertices, triangles), 4),
    }


def _mesh_bounds(vertices: Sequence[Sequence[float]]):
    if not vertices:
        return ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    mins = [min(v[i] for v in vertices) for i in range(3)]
    maxs = [max(v[i] for v in vertices) for i in range(3)]
    return (mins, maxs)


def _mesh_centroid(vertices: Sequence[Sequence[float]], triangles: Sequence[Sequence[int]]):
    if not vertices:
        return [0.0, 0.0, 0.0]
    if not triangles:
        return [sum(v[0] for v in vertices) / len(vertices),
                sum(v[1] for v in vertices) / len(vertices),
                sum(v[2] for v in vertices) / len(vertices)]
    cx = cy = cz = 0.0
    total = 0.0
    for tri in triangles:
        if len(tri) < 3:
            continue
        a = vertices[tri[0]]
        b = vertices[tri[1]]
        c = vertices[tri[2]]
        if not a or not b or not c:
            continue
        area = 0.5 * math.sqrt(
            ((b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1])) ** 2
            + ((b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2])) ** 2
            + ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) ** 2
        )
        cx += area * (a[0] + b[0] + c[0]) / 3.0
        cy += area * (a[1] + b[1] + c[1]) / 3.0
        cz += area * (a[2] + b[2] + c[2]) / 3.0
        total += area
    if total <= 0:
        return [0.0, 0.0, 0.0]
    return [cx / total, cy / total, cz / total]


def _mesh_volume(vertices: Sequence[Sequence[float]], triangles: Sequence[Sequence[int]]) -> float:
    """Signed tetrahedron volume sum; absolute value for closed meshes."""
    volume = 0.0
    for tri in triangles:
        if len(tri) < 3:
            continue
        a = vertices[tri[0]]
        b = vertices[tri[1]]
        c = vertices[tri[2]]
        if not a or not b or not c:
            continue
        volume += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        ) / 6.0
    return abs(volume)


def _primitive_mesh(geom_type: str, geometry: Mapping[str, Any]):
    """Synthesize a triangle mesh for analytic primitives (box/cylinder/...)."""

    def box_mesh(size):
        sx, sy, sz = (float(size[0]) / 2, float(size[1]) / 2, float(size[2]) / 2)
        verts = [
            [-sx, -sy, -sz], [sx, -sy, -sz], [sx, sy, -sz], [-sx, sy, -sz],
            [-sx, -sy, sz], [sx, -sy, sz], [sx, sy, sz], [-sx, sy, sz],
        ]
        tris = [
            [0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6],
            [0, 4, 5], [0, 5, 1], [1, 5, 6], [1, 6, 2],
            [2, 6, 7], [2, 7, 3], [3, 7, 4], [3, 4, 0],
        ]
        return verts, tris

    def cylinder_mesh(radius, height, segments=24):
        verts = []
        tris = []
        half = float(height) / 2
        for i in range(segments):
            theta = 2 * math.pi * i / segments
            x, y = radius * math.cos(theta), radius * math.sin(theta)
            verts.append([x, y, -half])
            verts.append([x, y, half])
        for i in range(segments):
            nxt = (i + 1) % segments
            tris.append([2 * i, 2 * nxt, 2 * i + 1])
            tris.append([2 * nxt, 2 * nxt + 1, 2 * i + 1])
        return verts, tris

    if geom_type == "box":
        return box_mesh(geometry.get("size") or [0.06, 0.04, 0.01])
    if geom_type in ("cylinder", "cone", "frustum"):
        radius = float(geometry.get("radius", geometry.get("radius_top", 0.01)) or 0.01)
        height = float(geometry.get("height", 0.01) or 0.01)
        return cylinder_mesh(radius, height)
    if geom_type == "sphere":
        radius = float(geometry.get("radius", 0.01) or 0.01)
        verts, tris = cylinder_mesh(radius, 2 * radius, segments=20)
        return verts, tris
    return [], []


# ---------------------------------------------------------------------------
# Thumbnail renderer (stdlib z-buffer rasterizer → PNG)
# ---------------------------------------------------------------------------


def render_part_thumbnail(
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    size: int = THUMBNAIL_SIZE,
) -> bytes:
    """Render a 3-view orthographic thumbnail (top, front, side) as PNG bytes."""
    if not vertices or not triangles:
        return _blank_png(size * THUMBNAIL_VIEWS, size)
    width = size * THUMBNAIL_VIEWS
    height = size
    pixels = [255] * (width * height * 3)
    views = ("top", "front", "side")
    max_tris = 1000
    if len(triangles) > max_tris:
        step = max(1, len(triangles) // max_tris)
        render_tris = triangles[::step]
    else:
        render_tris = triangles
    for view_index, view in enumerate(views):
        _rasterize_view(pixels, width, height, size, view_index * size, view, vertices, render_tris)
    return _png_from_rgb(pixels, width, height)


def _project(view: str, point: Sequence[float], bounds):
    """Orthographic projection of a world point into 2D pixel space for a view.

    Views: top (looking down −Z), front (looking along −Y), side (along +X).
    Right-handed z-up world frame, matching the rest of the pipeline.
    """
    x, y, z = point
    minx, miny, minz = bounds[0]
    maxx, maxy, maxz = bounds[1]
    if view == "top":
        u, v = x, y
        umin, umax, vmin, vmax = minx, maxx, miny, maxy
    elif view == "front":
        u, v = x, z
        umin, umax, vmin, vmax = minx, maxx, minz, maxz
    else:  # side
        u, v = y, z
        umin, umax, vmin, vmax = miny, maxy, minz, maxz
    span_u = max(1e-9, umax - umin)
    span_v = max(1e-9, vmax - vmin)
    return (u - umin) / span_u, (v - vmin) / span_v


def _rasterize_view(
    pixels: List[int],
    width: int,
    height: int,
    tile: int,
    offset_x: int,
    view: str,
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
):
    bounds = _mesh_bounds(vertices)
    depth_buffer = [float("inf")] * (tile * tile)
    light = (0.4, 0.6, 0.8)
    light_len = math.sqrt(sum(c * c for c in light))
    light = tuple(c / light_len for c in light)
    for tri in triangles:
        if len(tri) < 3:
            continue
        a = vertices[tri[0]]
        b = vertices[tri[1]]
        c = vertices[tri[2]]
        if not a or not b or not c:
            continue
        # Flat lambert shade from the world normal.
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nlen < 1e-12:
            continue
        nx, ny, nz = nx / nlen, ny / nlen, nz / nlen
        shade = max(0.15, abs(nx * light[0] + ny * light[1] + nz * light[2]))
        gray = int(235 * shade)
        p0 = _project(view, a, bounds)
        p1 = _project(view, b, bounds)
        p2 = _project(view, c, bounds)
        # Depth for sorting: use the average of the projected depth axis.
        depth_axis = {"top": 2, "front": 1, "side": 0}[view]
        depth = (a[depth_axis] + b[depth_axis] + c[depth_axis]) / 3.0
        _fill_triangle(
            pixels, depth_buffer, width, height, tile, offset_x,
            p0, p1, p2, depth, gray,
        )


def _fill_triangle(
    pixels: List[int],
    depth_buffer: List[float],
    width: int,
    height: int,
    tile: int,
    offset_x: int,
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    depth: float,
    gray: int,
):
    xs = [p0[0] * tile, p1[0] * tile, p2[0] * tile]
    ys = [p0[1] * tile, p1[1] * tile, p2[1] * tile]
    min_x = max(0, int(min(xs)))
    max_x = min(tile - 1, int(max(xs)))
    min_y = max(0, int(min(ys)))
    max_y = min(tile - 1, int(max(ys)))
    for py in range(min_y, max_y + 1):
        for px in range(min_x, max_x + 1):
            if _point_in_triangle(px + 0.5, py + 0.5, xs, ys):
                index = py * tile + px
                if depth < depth_buffer[index]:
                    depth_buffer[index] = depth
                    px_out = offset_x + px
                    base = (py * width + px_out) * 3
                    pixels[base] = gray
                    pixels[base + 1] = gray
                    pixels[base + 2] = gray


def _point_in_triangle(px: float, py: float, xs, ys) -> bool:
    (x0, y0), (x1, y1), (x2, y2) = (xs[0], ys[0]), (xs[1], ys[1]), (xs[2], ys[2])
    d1 = (px - x1) * (y0 - y1) - (x0 - x1) * (py - y1)
    d2 = (px - x2) * (y1 - y2) - (x1 - x2) * (py - y2)
    d3 = (px - x0) * (y2 - y0) - (x2 - x0) * (py - y0)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def _png_from_rgb(pixels: Sequence[int], width: int, height: int) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        for x in range(width):
            base = (y * width + x) * 3
            raw.extend((pixels[base], pixels[base + 1], pixels[base + 2]))
    compressed = zlib.compress(bytes(raw), 6)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


def _blank_png(width: int, height: int) -> bytes:
    return _png_from_rgb([255] * (width * height * 3), width, height)


# ---------------------------------------------------------------------------
# OpenRouter client
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    taxonomy = ", ".join(sorted(label for label in CANONICAL_COMPONENT_TYPES if label not in ("unresolved", "compound")))
    return (
        "You are a world-class mechanical engineer and computer mouse teardown expert.\n"
        "You classify computer mouse assembly CAD parts into exactly one component type from this taxonomy:\n"
        + taxonomy + ".\n\n"
        "Taxonomy Definition & Spatial Positioning in Computer Mice:\n"
        "- top_shell: Main upper palm cover spanning the full mouse body (~90-130mm long), high elevation (top roof).\n"
        "- bottom_shell: Main underside base plate spanning full mouse body (~90-130mm long), lowest elevation (Z=0) with sensor cutouts & skate recesses.\n"
        "- main_button: Left or right click cover/paddle, high elevation, forward longitudinal position (front of mouse).\n"
        "- side_button: Thumb buttons (forward/back) located on the lateral side of the mouse (typically left side for right-handed mice).\n"
        "- scroll_wheel: Cylindrical or ring-shaped wheel (diameter 20-28mm, width 5-9mm) with spokes or grip ridges, front-center position.\n"
        "- encoder: Rotary wheel encoder module, axle support, or optical encoder wheel bracket.\n"
        "- pcb: Printed circuit board or flexible PCB (thin planar board <2mm with cutouts, traces, mounting holes) at lower-to-mid internal elevation.\n"
        "- sensor: Optical tracking sensor IC package, prism lens, or IR optic assembly on the lower PCB.\n"
        "- battery: Rechargeable lithium pouch cell (rectangular block, e.g. 602024, 300mAh, 500mAh) or cylindrical battery in the central cavity.\n"
        "- foot_pad: Ultra-thin (<1mm) smooth PTFE glide skate on the bottom shell underside.\n"
        "- internal_structure: Internal chassis frame, skeleton, battery tray, light pipe/guide (TG), structural ribbing, or mounting bracket.\n"
        "- screw_boss: Cylindrical screw pillar, standoff, or fastener insert.\n\n"
        "Output STRICT JSON ONLY, one JSON object per part in the requested order:\n"
        '{"object_id": str, "component_type": str, "confidence": float 0..1, "reasons": [str, ...]}\n'
        "Rules:\n"
        "- Use the 3-view orthographic image (top, front, side) and assembly spatial position (elevation, front/rear, size in mm).\n"
        "- Mirrored left/right parts share the same component_type.\n"
        "- Give specific engineering reasons citing shape, dimensions, and spatial location.\n"
        "- component_type must be one of the taxonomy values or 'unresolved'.\n"
    )


def build_user_prompt(part: Mapping[str, Any], assembly_bounds: Optional[Tuple[Sequence[float], Sequence[float]]] = None) -> str:
    descriptor = part.get("descriptor") or {}
    bounds = descriptor.get("bounds_m") or [0, 0, 0, 0, 0, 0]

    spatial_loc = {}
    if assembly_bounds and len(assembly_bounds[0]) >= 3 and len(assembly_bounds[1]) >= 3:
        amin, amax = assembly_bounds
        z_span = max(1e-6, amax[2] - amin[2])
        y_span = max(1e-6, amax[1] - amin[1])
        x_span = max(1e-6, amax[0] - amin[0])
        part_cz = ((bounds[2] + bounds[5]) / 2.0 - amin[2]) / z_span
        part_cy = ((bounds[1] + bounds[4]) / 2.0 - amin[1]) / y_span
        part_cx = ((bounds[0] + bounds[3]) / 2.0 - (amin[0] + amax[0]) / 2.0)

        if part_cz < 0.15:
            elev = "bottom_floor (Z: 0-15%)"
        elif part_cz < 0.45:
            elev = "lower_internal (Z: 15-45%)"
        elif part_cz < 0.75:
            elev = "mid_internal (Z: 45-75%)"
        else:
            elev = "top_roof (Z: 75-100%)"

        if part_cy > 0.60:
            y_pos = "front_buttons (Y: 60-100%)"
        elif part_cy > 0.30:
            y_pos = "middle_chassis (Y: 30-60%)"
        else:
            y_pos = "rear_palm (Y: 0-30%)"

        spatial_loc = {
            "assembly_elevation": elev,
            "longitudinal_position": y_pos,
            "lateral_offset_mm": round(part_cx * 1000, 1),
            "mouse_total_size_mm": [round(x_span * 1000, 1), round(y_span * 1000, 1), round(z_span * 1000, 1)],
        }

    summary = {
        "object_id": part.get("object_id"),
        "name": descriptor.get("name_raw"),
        "geometry": {
            "type": descriptor.get("geometry_type"),
            "size_mm": [round(v * 1000, 1) for v in descriptor.get("size_m", [0, 0, 0])],
            "max_dim_mm": round((descriptor.get("max_dim_m") or 0) * 1000, 1),
            "flatness": descriptor.get("flatness"),
            "surface_area_cm2": round((descriptor.get("surface_area_m2") or 0) * 1e4, 2),
            "volume_cm3": round((descriptor.get("volume_m3") or 0) * 1e6, 3),
            "footprint_coverage": descriptor.get("footprint_coverage"),
            "mirror_symmetry_x": descriptor.get("mirror_symmetry_x"),
            "vertex_count": descriptor.get("vertex_count"),
            "triangle_count": descriptor.get("triangle_count"),
        },
        "spatial_context": spatial_loc,
    }
    return (
        "Classify this part. The image shows top/front/side orthographic views.\n"
        + json.dumps(summary)
    )


def _http_post_json(url: str, payload: Dict[str, Any], api_key_value: str, timeout: float) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": "Bearer " + api_key_value,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/mousetesting",
            "X-Title": "mouse-sim ai classify",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body.decode("utf-8"))


def api_endpoint() -> str:
    """Read the active endpoint, defaulting to OpenRouter."""
    ep = (
        os.environ.get("AI_CLASSIFY_ENDPOINT")
        or os.environ.get("OPENROUTER_ENDPOINT")
        or OPENROUTER_ENDPOINT
    ).strip()
    return ep


def call_openrouter(
    parts: Sequence[Mapping[str, Any]],
    api_key_value: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    endpoint: Optional[str] = None,
    timeout: float = 45.0,
    retries: int = 2,
    assembly_bounds: Optional[Tuple[Sequence[float], Sequence[float]]] = None,
) -> List[Optional[Mapping[str, Any]]]:
    """Call OpenRouter or any local OpenAI-compatible endpoint (e.g. LM Studio, Ollama)."""
    raw_endpoint = (endpoint or "").strip() or api_endpoint()
    target_endpoint = raw_endpoint
    if not target_endpoint.endswith("/chat/completions"):
        if not target_endpoint.endswith("/v1") and not target_endpoint.endswith("/v1/"):
            target_endpoint = target_endpoint.rstrip("/") + "/v1"
        target_endpoint = target_endpoint.rstrip("/") + "/chat/completions"

    is_local = any(
        h in target_endpoint
        for h in ("192.168.", "10.", "172.16.", "127.0.0.1", "localhost", "10.0.", "http://")
    )
    key = (api_key_value or api_key() or "").strip()
    if key.startswith("sk-lm-") and "openrouter.ai" in target_endpoint:
        target_endpoint = "http://127.0.0.1:1234/v1/chat/completions"
        is_local = True
    if not key and is_local:
        key = "local-not-needed"
    if not key:
        return []
    parts = list(parts)
    if not parts:
        return []
    # Fail fast on a dead endpoint: retrying a refused connection with
    # exponential backoff turns every analysis into a 6+ s stall (the local
    # LM Studio endpoint from a stray sk-lm- key is commonly down).  The
    # pipeline falls back to the deterministic rule classification, so a
    # drop test must never wait on an unreachable AI service.
    if _ENDPOINT_DOWN_CACHE is not None:
        with _ENDPOINT_DOWN_LOCK:
            down_since = _ENDPOINT_DOWN_CACHE.get(target_endpoint)
            if down_since is not None and time.monotonic() - down_since < _ENDPOINT_DOWN_TTL_S:
                return []
    chosen_model = model or model_name()
    chosen_provider = provider or provider_name()
    results: List[Optional[Mapping[str, Any]]] = [None] * len(parts)
    attempt = 0
    while attempt <= retries:
        try:
            # Build vision content payload
            content: List[Dict[str, Any]] = [{"type": "text", "text": build_system_prompt()}]
            for part in parts:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,"
                            + base64.b64encode(part["thumbnail_png"]).decode("ascii")
                        },
                    }
                )
                content.append({"type": "text", "text": build_user_prompt(part, assembly_bounds)})
            payload: Dict[str, Any] = {
                "model": chosen_model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0,
                "max_tokens": 1400,
            }
            if chosen_provider and "openrouter.ai" in target_endpoint:
                payload["provider"] = {
                    "order": [chosen_provider],
                    "allow_fallbacks": False,
                }
            try:
                response = _http_post_json(target_endpoint, payload, key, timeout)
            except urllib.error.HTTPError as http_err:
                if http_err.code in (401, 403):
                    # Bad/expired API key or wrong endpoint: abort AI calls immediately
                    break
                # If local model is text-only (doesn't support image_url), retry with text-only payload
                if http_err.code in (400, 404, 422):
                    text_content = build_system_prompt() + "\n\n" + "\n\n".join(
                        build_user_prompt(p, assembly_bounds) for p in parts
                    )
                    payload["messages"] = [{"role": "user", "content": text_content}]
                    response = _http_post_json(target_endpoint, payload, key, timeout)
                else:
                    raise
            except (urllib.error.URLError, ConnectionError) as conn_err:
                # Connection refused / DNS failure / reset: the endpoint is
                # unreachable — retrying cannot help.  Record it and fall back
                # to rule classification immediately.
                reason = getattr(conn_err, "reason", None)
                if isinstance(reason, (ConnectionRefusedError, ConnectionResetError, socket.gaierror)):
                    if _ENDPOINT_DOWN_CACHE is not None:
                        with _ENDPOINT_DOWN_LOCK:
                            _ENDPOINT_DOWN_CACHE[target_endpoint] = time.monotonic()
                    break
                raise

            text = (
                response.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            parsed = parse_classify_response(str(text))
            for index, part in enumerate(parts):
                p_id = str(part.get("object_id"))
                item = parsed.get(p_id) or parsed.get(str(index)) or parsed.get(f"part-{index}")
                if item is not None:
                    res_item = dict(item)
                    res_item["object_id"] = p_id
                    results[index] = res_item
            # Success (even partial) ends the retry loop.
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            if attempt >= retries:
                break
            attempt += 1
            time.sleep(1.0 * (2 ** attempt))
    return results


def parse_classify_response(text: str) -> Dict[str, Mapping[str, Any]]:
    """Robustly parse the LLM JSON response into {object_id: classification}."""
    parsed = _extract_json_object(text)
    items: List[Any] = []
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        if "results" in parsed and isinstance(parsed["results"], list):
            items = parsed["results"]
        elif "parts" in parsed and isinstance(parsed["parts"], list):
            items = parsed["parts"]
        elif "object_id" in parsed:
            items = [parsed]
        else:
            for k, v in parsed.items():
                if not isinstance(v, dict):
                    # Degenerate LLM output (e.g. {"part-0": "top_shell"}):
                    # skip non-object entries rather than crashing the job.
                    continue
                row = dict(v)
                row.setdefault("object_id", k)
                items.append(row)
    out: Dict[str, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        object_id = item.get("object_id") or item.get("id")
        if not object_id:
            continue
        label = canonical_component_type(str(item.get("component_type", "unresolved")))
        try:
            confidence = min(1.0, max(0.0, float(item.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        reasons = item.get("reasons")
        if not isinstance(reasons, list):
            reasons = []
        out[str(object_id)] = {
            "object_id": str(object_id),
            "component_type": label,
            "confidence": confidence,
            "reasons": [str(reason) for reason in reasons],
        }
    return out


def _extract_json_object(text: str):
    text = str(text).strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    pos_brace = text.find("{")
    pos_bracket = text.find("[")
    delimiters = []
    if pos_brace >= 0 and (pos_bracket < 0 or pos_brace < pos_bracket):
        delimiters = [("{", "}"), ("[", "]")]
    elif pos_bracket >= 0:
        delimiters = [("[", "]"), ("{", "}")]
    else:
        delimiters = [("{", "}"), ("[", "]")]

    for start_char, end_char in delimiters:
        start = text.find(start_char)
        if start >= 0:
            depth = 0
            in_string = False
            escape = False
            for index in range(start, len(text)):
                char = text[index]
                if in_string:
                    if escape:
                        escape = False
                    elif char == "\\":
                        escape = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == start_char:
                    depth += 1
                elif char == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : index + 1])
                        except (json.JSONDecodeError, TypeError, ValueError):
                            break
    return None


# ---------------------------------------------------------------------------
# Disk caching for classifications
# ---------------------------------------------------------------------------


def part_hash(descriptor: Mapping[str, Any], thumbnail: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(PROMPT_VERSION.encode("utf-8"))
    digest.update(json.dumps(descriptor, sort_keys=True, default=str).encode("utf-8"))
    digest.update(thumbnail)
    return digest.hexdigest()


class ClassificationCache:
    """Thread-safe disk cache keyed by ``part_hash``."""

    def __init__(self, directory: Optional[Path] = None, capacity: Optional[int] = None):
        self.directory = directory or cache_dir()
        self.capacity = capacity or cache_capacity()
        self._lock = threading.Lock()
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _path(self, key: str) -> Path:
        return self.directory / (key + ".json")

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        # Serialize against put()'s tmp+rename sequence so a reader can never
        # interleave mid-write, and validate shape so corrupt entries are
        # treated as misses instead of poisoning downstream consensus.
        with self._lock:
            try:
                with self._path(key).open("r", encoding="utf-8") as stream:
                    payload = json.load(stream)
            except (OSError, ValueError, TypeError):
                return None
            if not isinstance(payload, dict) or not payload.get("component_type"):
                return None
            try:
                confidence = float(payload.get("confidence", 0.0))
            except (TypeError, ValueError):
                return None
            if not 0.0 <= confidence <= 1.0:
                return None
            return payload

    def put(self, key: str, classification: Mapping[str, Any]) -> None:
        with self._lock:
            try:
                tmp = self.directory / ("." + key + ".tmp")
                with tmp.open("w", encoding="utf-8") as stream:
                    json.dump(dict(classification), stream)
                tmp.replace(self._path(key))
            except OSError:
                return

    def prune(self) -> None:
        with self._lock:
            try:
                entries = sorted(
                    (
                        (path.stat().st_mtime, path)
                        for path in self.directory.glob("*.json")
                    ),
                    key=lambda entry: entry[0],
                    reverse=True,
                )
            except OSError:
                return
            for _, path in entries[self.capacity :]:
                try:
                    path.unlink()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Fusion & consensus
# ---------------------------------------------------------------------------


def merge_classification(
    object_id: str,
    rule: Mapping[str, Any],
    ai: Optional[Mapping[str, Any]] = None,
    request: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Fuse rule + AI + user-request signals per the consensus matrix."""
    if request is not None and isinstance(request, dict):
        label = canonical_component_type(str(request.get("component_type", "unresolved")))
        try:
            confidence = min(1.0, max(0.0, float(request.get("confidence", 0.95))))
        except (TypeError, ValueError):
            confidence = 0.95
        return {
            "object_id": object_id,
            "component_type": label,
            "confidence": confidence,
            "source": "user",
            "needs_review": False,
            "reasons": ["user-provided classification"],
        }

    rule_label = canonical_component_type(str(rule.get("component_type", "unresolved")))
    rule_conf = min(1.0, max(0.0, float(rule.get("confidence", 0.0))))
    ai_result = ai if isinstance(ai, dict) else None
    if ai_result is None:
        return {
            "object_id": object_id,
            "component_type": rule_label,
            "confidence": rule_conf,
            "source": "heuristic",
            "needs_review": False,
            "reasons": list(rule.get("reasons") or ["deterministic rule classifier"]),
        }
    ai_label = canonical_component_type(str(ai_result.get("component_type", "unresolved")))
    try:
        ai_conf = min(1.0, max(0.0, float(ai_result.get("confidence", 0.0))))
    except (TypeError, ValueError):
        ai_conf = 0.0
    reasons = list(ai_result.get("reasons") or []) + ["openrouter vision"]
    if ai_conf >= 0.85:
        if ai_label == rule_label or rule_label in ("unresolved", "unknown", ""):
            return {
                "object_id": object_id,
                "component_type": ai_label,
                "confidence": min(0.98, max(ai_conf, rule_conf)),
                "source": "openrouter_vision",
                "needs_review": False,
                "reasons": reasons,
            }
        return {
            "object_id": object_id,
            "component_type": rule_label,
            "confidence": min(ai_conf, rule_conf) * 0.6,
            "source": "heuristic",
            "needs_review": True,
            "reasons": reasons + ["AI disagrees with rule; rule kept conservatively"],
        }
    final_label = ai_label if ai_label not in ("unresolved", "") else (rule_label if rule_label not in ("unresolved", "") else "internal_structure")
    final_conf = ai_conf if ai_label not in ("unresolved", "") else (rule_conf if rule_conf > 0 else 0.85)
    needs_review = (final_conf < 0.80)
    if final_label == "internal_structure" and ai_label in ("unresolved", "") and rule_label in ("unresolved", ""):
        reasons = reasons + ["Internal chassis / structure assigned from assembly geometry"]
    return {
        "object_id": object_id,
        "component_type": final_label,
        "confidence": final_conf,
        "source": "openrouter_vision" if (ai_label not in ("unresolved", "") and ai_conf >= 0.80) else "heuristic",
        "needs_review": needs_review,
        "reasons": reasons,
    }


def classify_parts(
    parts: Sequence[Mapping[str, Any]],
    use_cache: bool = True,
    cache: Optional[ClassificationCache] = None,
    api_key_value: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    endpoint: Optional[str] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> List[Dict[str, Any]]:
    """Classify a sequence of parts with the full cascade."""
    cache_obj = cache if cache is not None else ClassificationCache()
    merged: List[Optional[Dict[str, Any]]] = [None] * len(parts)

    def _prep(item):
        idx, p = item
        verts, trs = _sanitize_mesh(p.get("geometry") or {})
        desc = part_descriptors(p.get("geometry") or {}, p.get("name"))
        thumb = render_part_thumbnail(verts, trs)
        k = part_hash(desc, thumb)
        rule_hint = rule_classify_name(p.get("name"))
        rule = p.get("rule")
        if not rule or rule.get("component_type") in ("unresolved", "unknown", "") or float(rule.get("confidence", 0.0)) == 0.0:
            if rule_hint:
                rule = rule_hint
            else:
                rule = {"component_type": "unresolved", "confidence": 0.0}
        return idx, p, verts, trs, desc, thumb, k, rule

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as executor:
        prepared = list(executor.map(_prep, enumerate(parts)))

    # Compute overall assembly bounds across all parts
    minx = miny = minz = float("inf")
    maxx = maxy = maxz = float("-inf")
    for _, _, _, _, desc, _, _, _ in prepared:
        b = desc.get("bounds_m")
        if b and len(b) >= 6:
            minx = min(minx, b[0])
            miny = min(miny, b[1])
            minz = min(minz, b[2])
            maxx = max(maxx, b[3])
            maxy = max(maxy, b[4])
            maxz = max(maxz, b[5])
    assembly_bounds = ([minx, miny, minz], [maxx, maxy, maxz]) if minx != float("inf") else None

    # Enhance any unresolved rule with geometric classification
    enriched_prepared = []
    for idx, part, verts, trs, desc, thumb, k, rule in prepared:
        if not rule or rule.get("component_type") in ("unresolved", "unknown", "") or float(rule.get("confidence", 0.0)) == 0.0:
            geom_hint = rule_classify_geometry(desc, assembly_bounds, part.get("name"))
            if geom_hint:
                rule = geom_hint
        enriched_prepared.append((idx, part, verts, trs, desc, thumb, k, rule))
    prepared = enriched_prepared

    pending: List[Tuple[int, Mapping[str, Any], bytes, Dict[str, Any], str, Dict[str, Any]]] = []
    cached_count = 0
    for idx, part, verts, trs, desc, thumb, key, rule in prepared:
        object_id = str(part.get("object_id", "part-{}".format(idx)))
        request = part.get("request")
        if request is not None:
            merged[idx] = merge_classification(object_id, rule, None, request)
            cached_count += 1
            continue
        if use_cache:
            cached = cache_obj.get(key)
            if cached is not None:
                cached = dict(cached)
                cached["cached"] = True
                cached["object_id"] = object_id
                merged[idx] = merge_classification(object_id, rule, cached, None)
                cached_count += 1
                continue
        pending.append((idx, part, thumb, desc, key, rule))

    if on_progress:
        on_progress(cached_count, len(parts))

    is_custom_ep = bool(endpoint and str(endpoint).strip())
    enabled = bool(api_key_value) or is_enabled() or is_custom_ep
    if pending and enabled:
        done_count = cached_count
        for chunk_start in range(0, len(pending), MAX_BATCH_PARTS):
            chunk = pending[chunk_start : chunk_start + MAX_BATCH_PARTS]
            batch: List[Dict[str, Any]] = []
            for idx, part, thumb, desc, key, rule in chunk:
                batch.append(
                    {
                        "object_id": part.get("object_id", "part-{}".format(idx)),
                        "name": part.get("name"),
                        "thumbnail_png": thumb,
                        "descriptor": desc,
                    }
                )
            ai_results = call_openrouter(
                batch,
                api_key_value=api_key_value,
                model=model,
                provider=provider,
                endpoint=endpoint,
                assembly_bounds=assembly_bounds,
            )
            # Match by object_id, not position: call_openrouter returns one
            # slot per input part (None where the model skipped a part), so a
            # partial response must never shift classifications onto the
            # wrong parts.
            ai_by_id = {
                str(ai.get("object_id")): ai
                for ai in ai_results
                if ai is not None and ai.get("object_id") is not None
            }
            for idx, part, thumb, desc, key, rule in chunk:
                ai = ai_by_id.get(str(part.get("object_id", "part-{}".format(idx))))
                merged[idx] = merge_classification(
                    part.get("object_id", "part-{}".format(idx)),
                    rule,
                    ai,
                    None,
                )
                if ai is not None:
                    cache_obj.put(key, dict(ai))
                done_count += 1
            if on_progress:
                on_progress(done_count, len(parts))
    else:
        for idx, part, thumb, desc, key, rule in pending:
            merged[idx] = merge_classification(
                part.get("object_id", "part-{}".format(idx)),
                rule,
                None,
                None,
            )
        if on_progress:
            on_progress(len(parts), len(parts))
    cache_obj.prune()
    return [item for item in merged if item is not None]
