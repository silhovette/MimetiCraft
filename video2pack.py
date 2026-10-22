#!/usr/bin/env python3
"""MimetiCraft MVP: construction video -> approximate voxel scene -> Minecraft Java 26.3 datapack ZIP.

Use --video with ChatGPT Sign in (default) or an optional API key; --scene works offline.
No custom training. Video inference quality is not guaranteed.
"""
from __future__ import annotations

import argparse
import base64
import heapq
import json
import os
from pathlib import Path
import re
import sys
import time
import zipfile
from typing import Any, Callable

PACK_VERSION = [121, 0]  # Java 26.3
NAMESPACE = "vidbuild"
MAX_VOLUME = 200_000
MAX_OPS = 500
MAX_FILL = 4096
MAX_COMMANDS = 50_000
MAX_FRAMES = 1200
DEFAULT_FRAMES = 500

# Conservative set of vanilla Java block IDs. More can be added without touching compiler logic.
PALETTE = [
    "air", "stone", "cobblestone", "mossy_cobblestone", "stone_bricks", "mossy_stone_bricks",
    "cracked_stone_bricks", "andesite", "polished_andesite", "diorite", "polished_diorite",
    "granite", "polished_granite", "deepslate", "cobbled_deepslate", "deepslate_bricks",
    "deepslate_tiles", "tuff", "tuff_bricks", "bricks", "mud_bricks", "sandstone",
    "smooth_sandstone", "quartz_block", "smooth_quartz", "quartz_bricks", "white_concrete",
    "light_gray_concrete", "gray_concrete", "black_concrete", "brown_concrete", "red_concrete",
    "yellow_concrete", "blue_concrete", "green_concrete", "orange_concrete", "cyan_concrete",
    "white_terracotta", "orange_terracotta", "terracotta", "white_wool", "black_wool",
    "glass", "tinted_glass", "white_stained_glass", "light_blue_stained_glass", "glass_pane",
    "white_stained_glass_pane", "oak_planks", "spruce_planks", "dark_oak_planks",
    "birch_planks", "jungle_planks", "acacia_planks", "mangrove_planks", "cherry_planks",
    "oak_log", "spruce_log", "dark_oak_log", "stripped_oak_log", "stripped_spruce_log",
    "stripped_dark_oak_log", "oak_leaves", "spruce_leaves", "birch_leaves", "oak_fence",
    "spruce_fence", "dark_oak_fence", "iron_bars", "iron_block", "gold_block", "copper_block",
    "cut_copper", "oxidized_copper", "stone_slab", "stone_brick_slab", "smooth_stone_slab",
    "quartz_slab", "oak_slab", "spruce_slab", "dark_oak_slab", "brick_slab",
    "deepslate_brick_slab", "stone_stairs", "stone_brick_stairs", "quartz_stairs",
    "oak_stairs", "spruce_stairs", "dark_oak_stairs", "brick_stairs", "deepslate_brick_stairs",
    "oak_door", "spruce_door", "iron_door", "oak_trapdoor", "spruce_trapdoor",
    "lantern", "sea_lantern", "glowstone", "end_rod", "grass_block", "dirt",
    "coarse_dirt", "gravel", "sand", "water", "bookshelf", "barrel", "crafting_table",
]
PALETTE_SET = {"minecraft:" + name for name in PALETTE}
BLOCK_RE = re.compile(r"^minecraft:([a-z0-9_]+)(?:\[([a-z0-9_=,]+)\])?$")
STATES_ALLOWED = {
    "facing": {"north", "south", "east", "west", "up", "down"},
    "half": {"top", "bottom", "upper", "lower"},
    "type": {"top", "bottom", "double"},
    "axis": {"x", "y", "z"},
    "open": {"true", "false"},
    "hinge": {"left", "right"},
    "powered": {"true", "false"},
    "waterlogged": {"true", "false"},
    "shape": {"straight", "inner_left", "inner_right", "outer_left", "outer_right"},
    "rotation": {str(i) for i in range(16)},
    "lit": {"true", "false"},
    "hanging": {"true", "false"},
}

SCENE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["name", "size", "ops"],
    "properties": {
        "name": {"type": "string"},
        "size": {"type": "array", "items": {"type": "integer"}},
        "ops": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["block", "from", "to"],
            "properties": {
                "block": {"type": "string"},
                "from": {"type": "array", "items": {"type": "integer"}},
                "to": {"type": "array", "items": {"type": "integer"}},
            },
        }},
    },
}


def check_block(block: str) -> str:
    if not isinstance(block, str):
        raise ValueError("block name must be a string")
    m = BLOCK_RE.fullmatch(block)
    if m is None or "minecraft:" + m.group(1) not in PALETTE_SET:
        raise ValueError(f"Unsupported block ID: {block}")
    states_raw = m.group(2)
    if states_raw:
        pairs = states_raw.split(",")
        seen = set()
        for pair in pairs:
            if pair.count("=") != 1:
                raise ValueError(f"Malformed block state: {block}")
            key, value = pair.split("=")
            if key in seen or key not in STATES_ALLOWED or value not in STATES_ALLOWED[key]:
                raise ValueError(f"Unsupported block state: {block}")
            seen.add(key)
        name = m.group(1)
        if "facing" in seen and not any(s in name for s in ["stairs", "door", "trapdoor", "end_rod"]):
            raise ValueError(f"Facing does not apply to {name}")
        if "type" in seen and not name.endswith("_slab"):
            raise ValueError(f"Slab type does not apply to {name}")
        if "shape" in seen and not name.endswith("_stairs"):
            raise ValueError(f"Stairs shape does not apply to {name}")
        if "half" in seen and not any(s in name for s in ["stairs", "door", "trapdoor"]):
            raise ValueError(f"Half does not apply to {name}")
        if "axis" in seen and not (name.endswith("_log") or name.endswith("_wood")):
            raise ValueError(f"Axis does not apply to {name}")
    return block


def parse_point(value: Any, name: str) -> tuple[int, int, int]:
    if not isinstance(value, list) or len(value) != 3 or any(type(v) is not int for v in value):
        raise ValueError(f"{name} must be [integer x, integer y, integer z]")
    return tuple(value)


def validate_scene(scene: Any) -> dict[str, Any]:
    if not isinstance(scene, dict):
        raise ValueError("Scene must be a JSON object")
    dims = parse_point(scene.get("size"), "size")
    if any(not (1 <= d <= lim) for d, lim in zip(dims, (64, 48, 64))):
        raise ValueError("MVP dimensions limited to 64 x 48 x 64")
    if dims[0] * dims[1] * dims[2] > MAX_VOLUME:
        raise ValueError("Scene too large for first MVP")
    ops = scene.get("ops")
    if not isinstance(ops, list) or not 1 <= len(ops) <= MAX_OPS:
        raise ValueError(f"Scene requires 1..{MAX_OPS} ops")
    validated = []
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            raise ValueError(f"Operation {i} isn't a JSON object")
        block = check_block(op.get("block"))
        a = parse_point(op.get("from"), f"ops[{i}].from")
        b = parse_point(op.get("to"), f"ops[{i}].to")
        if any(a[j] > b[j] or a[j] < 0 or b[j] >= dims[j] for j in range(3)):
            raise ValueError(f"Invalid bounds in operation {i}: {a}..{b} within {dims}")
        if block == "minecraft:air" and (b[0] - a[0] + 1)*(b[1] - a[1] + 1)*(b[2] - a[2] + 1) > 1000:
            raise ValueError(f"Air clearing too large in operation {i}; prevents terrain damage")
        validated.append({"block": block, "from": list(a), "to": list(b)})
    return {"name": str(scene.get("name") or "video_build")[:80], "size": list(dims), "ops": validated}


def split_fill(a: tuple[int, ...], b: tuple[int, ...]):
    """Yield inclusive cuboids whose volumes are <= MAX_FILL."""
    volume = 1
    for i in range(3):
        volume *= b[i] - a[i] + 1
    if volume <= MAX_FILL:
        yield a, b
        return
    largest = max(range(3), key=lambda i: b[i] - a[i])
    if a[largest] == b[largest]:
        raise ValueError("Cannot split region")
    mid = (a[largest] + b[largest]) // 2
    left_b = list(b)
    left_b[largest] = mid
    right_a = list(a)
    right_a[largest] = mid + 1
    yield from split_fill(a, tuple(left_b))
    yield from split_fill(tuple(right_a), b)


def make_commands(scene: dict[str, Any]) -> list[str]:
    lines = []
    for op in scene["ops"]:
        block = op["block"]
        for a, b in split_fill(tuple(op["from"]), tuple(op["to"])):
            if a == b:
                lines.append(f"setblock ~{a[0]} ~{a[1]} ~{a[2]} {block}")
            else:
                lines.append(f"fill ~{a[0]} ~{a[1]} ~{a[2]} ~{b[0]} ~{b[1]} ~{b[2]} {block}")
    if len(lines) > MAX_COMMANDS:
        raise ValueError("Too many commands for first MVP")
    return lines


def make_undo_commands(scene: dict[str, Any]) -> list[str]:
    """Remove placed block types inside each operation's footprint.

    This is a destructive *removal*, not a world snapshot. It cannot restore
    terrain/blocks that were overwritten by the build.
    """
    commands = []
    for op in reversed(scene["ops"]):
        block = op["block"]
        if block == "minecraft:air":
            continue
        # Ignore states when matching to tolerate automatic stair/door updates.
        block_id = block.split("[", 1)[0]
        for a, b in split_fill(tuple(op["from"]), tuple(op["to"])):
            if a == b:
                commands.append(
                    f"execute if block ~{a[0]} ~{a[1]} ~{a[2]} {block_id} "
                    f"run setblock ~{a[0]} ~{a[1]} ~{a[2]} minecraft:air"
                )
            else:
                commands.append(
                    f"fill ~{a[0]} ~{a[1]} ~{a[2]} ~{b[0]} ~{b[1]} ~{b[2]} "
                    f"minecraft:air replace {block_id}"
                )
    if len(commands) > MAX_COMMANDS:
        raise ValueError("Too many undo commands for this MVP")
    return commands


def compile_datapack(scene: dict[str, Any], output: Path, offset: tuple[int, int, int] = (5, 0, 5)) -> dict[str, Any]:
    scene = validate_scene(scene)
    forward = make_commands(scene)
    reverse = make_undo_commands(scene)
    output.parent.mkdir(parents=True, exist_ok=True)
    pack = {"pack": {"description": f"MimetiCraft build/undo: {scene['name']}",
                     "min_format": PACK_VERSION, "max_format": PACK_VERSION}}
    build_chunks = [forward[i:i + 400] for i in range(0, len(forward), 400)]
    undo_chunks = [reverse[i:i + 400] for i in range(0, len(reverse), 400)]

    # The last origin is saved as precise NBT coordinates + dimension. Do not
    # allow a second build while one is active; this avoids losing undo state.
    build = (
        "# Only one pending build per world. Undo before building again.\n"
        'execute if data storage vidbuild:history last run tellraw @s {"text":"A build is pending. Run /function vidbuild:undo first.","color":"yellow"}\n'
        'execute unless data storage vidbuild:history last if dimension minecraft:overworld run function vidbuild:internal/start\n'
        'execute unless data storage vidbuild:history last if dimension minecraft:the_nether run function vidbuild:internal/start\n'
        'execute unless data storage vidbuild:history last if dimension minecraft:the_end run function vidbuild:internal/start\n'
        'execute unless dimension minecraft:overworld unless dimension minecraft:the_nether unless dimension minecraft:the_end run tellraw @s {"text":"Only the three vanilla dimensions are supported by tracked undo.","color":"red"}\n'
    )
    start = (
        f"# Align position to block corner; origin offset = {offset}.\n"
        f"execute align xyz positioned ~{offset[0]} ~{offset[1]} ~{offset[2]} "
        'summon minecraft:marker run function vidbuild:internal/build_at_origin\n'
    )
    # /execute summon sets @s to the transient marker; access its exact Pos
    # with /data modify ... set from entity, avoiding integer rounding bugs.
    build_at_origin = (
        'data modify storage vidbuild:history last set value {dimension:"minecraft:overworld",x:0.0d,y:0.0d,z:0.0d}\n'
        'execute if dimension minecraft:the_nether run data modify storage vidbuild:history last.dimension set value "minecraft:the_nether"\n'
        'execute if dimension minecraft:the_end run data modify storage vidbuild:history last.dimension set value "minecraft:the_end"\n'
        'data modify storage vidbuild:history last.x set from entity @s Pos[0]\n'
        'data modify storage vidbuild:history last.y set from entity @s Pos[1]\n'
        'data modify storage vidbuild:history last.z set from entity @s Pos[2]\n'
        'function vidbuild:parts/all\n'
        'kill @s\n'
    )
    # Test for missing state before a successful undo removes that state.
    undo = (
        'execute unless data storage vidbuild:history last run tellraw @s {"text":"Nothing to undo; use /function vidbuild:build first.","color":"yellow"}\n'
        'execute if data storage vidbuild:history last run function vidbuild:internal/undo_from_saved_origin with storage vidbuild:history last\n'
    )
    # Function macros are supported by Java since 1.20.2. This can locate the
    # previous build even if the player has moved or changed dimension.
    undo_macro = (
        '# Dynamic origin and dimension from vidbuild:history last.\n'
        '$execute in $(dimension) positioned $(x) $(y) $(z) run function vidbuild:internal/undo_at_origin\n'
    )
    max_x, _, max_z = [n-1 for n in scene["size"]]
    # 4 corner checks prevent deletion of the undo record if building chunks
    # are unloaded. Stay nearby when undoing. The last Y is already in range
    # because the user originally built there.
    undo_at_origin = (
        'data modify storage vidbuild:history loaded set value {ok:0b}\n'
        f'execute if loaded ~0 ~0 ~0 if loaded ~{max_x} ~0 ~0 '
        f'if loaded ~0 ~0 ~{max_z} if loaded ~{max_x} ~0 ~{max_z} '
        'run data modify storage vidbuild:history loaded.ok set value 1b\n'
        'execute if data storage vidbuild:history loaded{ok:1b} run function vidbuild:internal/commit_undo\n'
        'execute unless data storage vidbuild:history loaded{ok:1b} run tellraw @s {"text":"Undo paused: return near the structure so its chunks are loaded, then retry.","color":"yellow"}\n'
        'data remove storage vidbuild:history loaded\n'
    )
    commit_undo = (
        'function vidbuild:parts/undo_all\n'
        'data remove storage vidbuild:history last\n'
        'tellraw @s {"text":"Build removed. Previously overwritten terrain was NOT restored.","color":"green"}\n'
    )

    base = f"data/{NAMESPACE}/function"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("pack.mcmeta", json.dumps(pack, ensure_ascii=False, indent=2) + "\n")
        z.writestr(f"{base}/build.mcfunction", build)
        z.writestr(f"{base}/undo.mcfunction", undo)
        z.writestr(f"{base}/check.mcfunction",
                   'tellraw @s {"text":"MimetiCraft 26.3 ready: /function vidbuild:build and /function vidbuild:undo","color":"green"}\n')
        z.writestr(f"{base}/internal/start.mcfunction", start)
        z.writestr(f"{base}/internal/build_at_origin.mcfunction", build_at_origin)
        z.writestr(f"{base}/internal/undo_from_saved_origin.mcfunction", undo_macro)
        z.writestr(f"{base}/internal/undo_at_origin.mcfunction", undo_at_origin)
        z.writestr(f"{base}/internal/commit_undo.mcfunction", commit_undo)
        z.writestr(f"{base}/parts/all.mcfunction",
                   "".join(f"function {NAMESPACE}:parts/part_{i:03d}\n" for i in range(len(build_chunks))))
        z.writestr(f"{base}/parts/undo_all.mcfunction",
                   "".join(f"function {NAMESPACE}:parts/undo_part_{i:03d}\n" for i in range(len(undo_chunks))))
        for i, commands in enumerate(build_chunks):
            z.writestr(f"{base}/parts/part_{i:03d}.mcfunction", "\n".join(commands) + "\n")
        for i, commands in enumerate(undo_chunks):
            z.writestr(f"{base}/parts/undo_part_{i:03d}.mcfunction", "\n".join(commands) + "\n")
    return {"zip": str(output), "scene": scene["name"], "size": scene["size"],
            "operations": len(scene["ops"]), "commands": len(forward),
            "undo_commands": len(reverse), "parts": len(build_chunks),
            "offset": list(offset), "undo_function": f"{NAMESPACE}:undo"}

def _sample_frame_ids(length: int, count: int) -> list[int]:
    """Distinct chronological samples, biased slightly toward later construction."""
    count = min(count, length)
    if count == 1:
        return [length - 1]
    ids = []
    for i in range(count):
        proposed = round((length - 1) * (i / (count - 1)) ** .72)
        # Reserve enough unused positions for the remaining samples.
        lower = ids[-1] + 1 if ids else 0
        ids.append(min(max(proposed, lower), length - count + i))
    return ids


def _frame_difference(first, second) -> float:
    """Cheap appearance difference; local tiles keep small changes relevant."""
    import numpy as np

    difference = np.abs(first.astype(np.float32) - second.astype(np.float32)).mean(axis=2)
    tiles = difference.reshape(6, 12, 8, 12).mean(axis=(1, 3)).ravel()
    local = np.partition(tiles, -4)[-4:].mean()
    return float((difference.mean() + local) / 2)


def _select_frame_indices(signatures, sharpness, target: int) -> list[int]:
    """Remove least-novel interior frames; update their surviving neighbors.

    This is an appearance heuristic, not semantic scene understanding. Keep the
    first and last decoded frames and return exactly target if enough exist.
    """
    total = len(signatures)
    if total <= target:
        return list(range(total))
    if target == 1:
        return [total - 1]
    previous = [i - 1 for i in range(total)]
    following = [i + 1 for i in range(total)]
    active = [True] * total
    versions = [0] * total
    edges = [_frame_difference(signatures[i], signatures[i + 1]) for i in range(total - 1)] + [0.0]
    heap = []

    def enqueue(index):
        if 0 < index < total - 1 and active[index]:
            versions[index] += 1
            novelty = min(edges[previous[index]], edges[index])
            heapq.heappush(heap, (novelty, sharpness[index], index, versions[index]))

    for index in range(1, total - 1):
        enqueue(index)
    remaining = total
    while remaining > target:
        _, _, index, version = heapq.heappop(heap)
        if not active[index] or version != versions[index]:
            continue
        left, right = previous[index], following[index]
        active[index] = False
        following[left], previous[right] = right, left
        edges[left] = _frame_difference(signatures[left], signatures[right])
        remaining -= 1
        enqueue(left)
        enqueue(right)
    return [index for index in range(total) if active[index]]


def video_frames(path: Path, count: int):
    if type(count) is not int or not 1 <= count <= MAX_FRAMES:
        raise ValueError(f"frame count must be 1..{MAX_FRAMES}")
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("Install dependencies: pip install -r requirements.txt") from exc
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open video: {path}")
    try:
        length = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if length <= 0:
            raise RuntimeError("Video has no readable frames")
        target = min(count, length)
        candidate_count = min((count * 11 + 9) // 10, length)
        frame_ids = _sample_frame_ids(length, candidate_count)
        frames = []
        signatures, sharpness = [], []
        # Dense sampling avoids repeatedly decoding the same keyframe interval.
        sequential = length <= candidate_count * 64
        next_frame = 0
        for frame_id in frame_ids:
            if sequential:
                while next_frame < frame_id:
                    if not capture.grab():
                        break
                    next_frame += 1
                if next_frame != frame_id:
                    break
            else:
                capture.set(cv2.CAP_PROP_POS_FRAMES, frame_id)
            ok, frame = capture.read()
            next_frame = frame_id + 1
            if not ok:
                continue
            height, width = frame.shape[:2]
            factor = min(1., 1280/max(width, height))
            if factor < 1:
                frame = cv2.resize(frame, (int(width*factor), int(height*factor)), interpolation=cv2.INTER_AREA)
            ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                frames.append((frame_id, jpeg.tobytes()))
                thumbnail = cv2.resize(frame, (96, 72), interpolation=cv2.INTER_AREA)
                signatures.append(thumbnail)
                gray = cv2.cvtColor(thumbnail, cv2.COLOR_BGR2GRAY)
                sharpness.append(float(cv2.Laplacian(gray, cv2.CV_32F).var()))
        if not frames:
            raise RuntimeError("Unable to decode frames")
        selected = _select_frame_indices(signatures, sharpness, target)
        result = [frames[index] for index in selected]
        print(f"Local frame filter: requested {count}, sampled {len(frames)}/{candidate_count} "
              f"candidates, removed {len(frames) - len(result)}, kept {len(result)}.", flush=True)
        if len(result) < count:
            print('Video has fewer readable sampled frames than requested; using all available samples.', flush=True)
        return result
    finally:
        capture.release()


class _ModelFailure(RuntimeError):
    def __init__(self, message: str, code: str = 'unknown_error'):
        super().__init__(message)
        self.code = code


def _report_progress(progress, message: str):
    print(message, flush=True)
    if progress:
        progress(message)


def _request_scene_text(client, request: dict, model: str, frame_count: int,
                        progress=None) -> str:
    """Retry a transient failure once, only before any model output arrives."""
    transient = {'server_error', 'internal_error', 'server_is_overloaded',
                 'service_unavailable', 'service_unavailable_error',
                 'rate_limit_exceeded', 'slow_down', 'stream_ended'}
    no_retry = {'subscription_sharing_usage_limit_exceeded', 'subscription_sharing_usage_unavailable',
                'insufficient_quota', 'context_length_exceeded', 'invalid_request_error',
                'invalid_prompt', 'response_incomplete'}
    for attempt in range(2):
        output_started = False
        http_status, request_id, retry_after = None, None, None

        def metadata(response):
            nonlocal http_status, request_id, retry_after
            if response is not None:
                http_status = getattr(response, 'status_code', http_status)
                headers = getattr(response, 'headers', {})
                request_id = headers.get('x-request-id') or request_id
                retry_after = headers.get('retry-after') or retry_after

        try:
            result = client.responses.create(**request)
            if not request.get('stream'):
                request_id = getattr(result, '_request_id', None)
                if getattr(result, 'status', None) == 'incomplete':
                    raise _ModelFailure('Model stopped before completing the scene.', 'response_incomplete')
                return result.output_text
            deltas = []
            completed = False
            with result as stream:
                metadata(getattr(stream, 'response', None))
                for event in stream:
                    kind = event.type
                    if kind.startswith(('response.output_', 'response.reasoning')):
                        output_started = True
                    if kind == 'response.output_text.delta':
                        if not deltas:
                            _report_progress(progress, 'Receiving building scene from OpenAI...')
                        deltas.append(event.delta)
                    elif kind in ('response.failed', 'error'):
                        error = getattr(getattr(event, 'response', None), 'error', None) or event
                        code = getattr(error, 'code', None) or 'unknown_error'
                        raise _ModelFailure('OpenAI reported a failed generation.', code)
                    elif kind == 'response.incomplete':
                        raise _ModelFailure('Model stopped before completing the scene.', 'response_incomplete')
                    elif kind == 'response.completed':
                        completed = True
                        if not deltas:
                            for item in (getattr(event.response, 'output', None) or []):
                                for part in (getattr(item, 'content', None) or []):
                                    if getattr(part, 'type', '') == 'output_text':
                                        deltas.append(part.text)
            if not completed:
                raise _ModelFailure('Stream ended without response.completed; no ZIP was written.', 'stream_ended')
            return ''.join(deltas)
        except Exception as exc:
            metadata(getattr(exc, 'response', None))
            request_id = getattr(exc, 'request_id', None) or request_id
            body = getattr(exc, 'body', None)
            error = body.get('error', body) if isinstance(body, dict) else {}
            if not isinstance(error, dict):
                error = {}
            code = getattr(exc, 'code', None) or error.get('code') or error.get('type') or 'unknown_error'
            # Some streaming APIError exceptions omit response headers and only
            # include the public request ID in their diagnostic message.
            match = re.search(r'\breq_[A-Za-z0-9_-]+', str(exc))
            request_id = request_id or (match.group() if match else None)
            network_error = type(exc).__name__ in ('APIConnectionError', 'APITimeoutError')
            retryable = code not in no_retry and (code in transient or network_error or
                                                 isinstance(http_status, int) and 500 <= http_status < 600)
            delay = 2.0
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except (TypeError, ValueError):
                    # Do not ignore an HTTP-date or unrecognized Retry-After.
                    delay = 31.0
            if retryable and not output_started and attempt == 0 and delay <= 30:
                _report_progress(progress, f'OpenAI temporary error ({code}); retrying once in {delay:g}s with the same frames and model...')
                time.sleep(delay)
                continue
            # Only expose bounded diagnostic metadata, never bodies, headers,
            # authorization credentials, input images, or partial scene text.
            safe_code = str(code) if re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', str(code)) else 'unknown_error'
            safe_id = str(request_id) if request_id and re.fullmatch(r'req_[A-Za-z0-9_-]{1,200}', str(request_id)) else 'unavailable'
            lines = ['OpenAI image analysis failed; no new ZIP was generated.',
                     f'Model: {model}', f'Frames submitted: {frame_count}',
                     f'Attempt: {attempt + 1}/2', f'HTTP status: {http_status or "unavailable"}',
                     f'Error code: {safe_code}', f'Request ID: {safe_id}']
            if isinstance(exc, _ModelFailure):
                lines.append(str(exc))
            if output_started:
                lines.append('Model output had already started. Not automatically replayed to avoid duplicate usage.')
            elif code in no_retry:
                lines.append('This error is not automatically retried. Check account limits, input size or model support.')
            elif retryable:
                lines.append('The temporary error persisted or the server requested a longer wait. Try again later.')
            else:
                lines.append('Retry only after checking the error details. If it repeats, try fewer frames or another listed model.')
            raise RuntimeError('\n'.join(lines)) from None


def infer_scene(video: Path, model: str, frame_count: int, api_key: str | None = None,
                auth_mode: str = "chatgpt", access_token: str | None = None,
                progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install dependencies: pip install -r requirements.txt") from exc
    if auth_mode not in ("chatgpt", "api"):
        raise ValueError("auth_mode must be chatgpt or api")
    if auth_mode == "chatgpt" and not access_token:
        raise RuntimeError("ChatGPT sign-in is required. Sign in from the desktop app.")
    if auth_mode == "api" and not (api_key or os.getenv("OPENAI_API_KEY")):
        raise RuntimeError("API key mode requires OPENAI_API_KEY")
    frames = video_frames(video, frame_count)
    _report_progress(progress, f"Extracted {len(frames)} frames; invoking {model}...")
    palette_text = ", ".join("minecraft:" + b for b in PALETTE if b != "air")
    prompt = f"""You are reconstructing a Minecraft building from ordered frames of its CONSTRUCTION VIDEO.
Your job: output a single JSON scene specifying approximate Minecraft blocks and grid coordinates.
This is a first MVP, so prefer a conservative, physically coherent small building instead of inventing hidden intricate details.
Frames arrive in chronological order. Interpret construction order to recover walls/floors/roof/doors/windows.
Coordinate system: x increases left-to-right when facing building front; y is vertical with lowest foundation at 0; z runs front-to-back, starting at 0.
All positions must be NONNEGATIVE integers strictly within scene size, max size 64 x 48 x 64. Whole build must fit inside size.
Represent contiguous blocks as inclusive cuboids {{'from':[x,y,z],'to':[x,y,z]}}. Use 1x1x1 ops for decorative details.
Ops execute IN ORDER, later ops replace earlier ones. A hollow house should be modeled with separate walls, not a solid volume.
Prioritize accuracy of silhouette, footprint, wall height, roof and visible surface materials. Avoid filling unrelated terrain with air.
Do NOT claim to know hidden details. For stairs: use state string e.g. minecraft:stone_brick_stairs[facing=north,half=bottom].
For slabs: e.g. minecraft:stone_brick_slab[type=bottom].
Only use block names from this allowed set (state suffix permitted on appropriate blocks): {palette_text}.
If you cannot reliably see a structure, make a simple coherent approximation. Return 20-250 ops where useful; never return empty.
Use JSON with top-level keys name, size, ops, each op block/from/to.
"""
    content = [{"type": "input_text", "text": prompt}]
    for index, (frame_id, jpg) in enumerate(frames):
        content.append({"type":"input_text", "text": f"Chronological frame {index+1} of {len(frames)} (video frame #{frame_id})"})
        content.append({"type":"input_image", "image_url":"data:image/jpeg;base64," + base64.b64encode(jpg).decode("ascii"), "detail":"high"})
    request = dict(model=model, input=[{"role": "user", "content": content}],
                   text={"format": {"type": "json_schema", "name": "minecraft_build_scene", "strict": True, "schema": SCENE_SCHEMA}})
    if auth_mode == "api":
        client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"), max_retries=0)
        request['max_output_tokens'] = 20000
    else:
        # OpenAI's Sign in with ChatGPT usage route specifically requires
        # store=False, stream=True and forbids max_output_tokens. It uses the
        # PUBLIC Responses API, never the ChatGPT backend-api.
        client = OpenAI(api_key=access_token, base_url="https://api.openai.com/v1", max_retries=0)
        request.update(store=False, stream=True)
    try:
        output_text = _request_scene_text(client, request, model, len(frames), progress)
    finally:
        close = getattr(client, 'close', None)
        if close:
            close()
    if not output_text:
        raise RuntimeError("Model did not return a JSON scene; try fewer frames or another model")
    try:
        return validate_scene(json.loads(output_text))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Model returned malformed JSON; try a different model or fewer frames") from exc



def run(*, video: str | None = None, scene_path: str | None = None, out: str = "video_build.zip",
        model: str | None = None, frames: int = DEFAULT_FRAMES, api_key: str | None = None,
        auth_mode: str = "chatgpt", access_token: str | None = None,
        offset: tuple[int, int, int] = (5, 0, 5),
        progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    if (video is None) == (scene_path is None):
        raise ValueError("Choose exactly one: video or scene_path")
    if not 2 <= frames <= MAX_FRAMES:
        raise ValueError(f"frames must be 2..{MAX_FRAMES}")
    if scene_path:
        scene = validate_scene(json.loads(Path(scene_path).read_text(encoding="utf8")))
    else:
        scene = infer_scene(Path(video), model or "gpt-5.5", frames, api_key,
                            auth_mode=auth_mode, access_token=access_token, progress=progress)
    _report_progress(progress, 'Scene received and validated. Writing Data Pack ZIP...')
    dest = Path(out)
    return compile_datapack(scene, dest, offset=offset)


def main():
    parser = argparse.ArgumentParser(description="Video -> Minecraft Java 26.3 datapack ZIP (MVP)")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", help="Construction video (MP4 etc.), ChatGPT login or API key")
    src.add_argument("--scene", help="Existing scene JSON, offline pack creation")
    parser.add_argument("--out", default="video_build.zip")
    parser.add_argument("--model", default=None, help="OAuth mode defaults to first account model")
    parser.add_argument("--auth", choices=("chatgpt", "api"), default="chatgpt", help="Video model authentication")
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES,
                        help=f"Sample 2..{MAX_FRAMES} frames (default: {DEFAULT_FRAMES})")
    parser.add_argument("--offset", nargs=3, type=int, default=(5,0,5), metavar=("X","Y","Z"))
    args = parser.parse_args()
    try:
        access_token = None
        model = args.model
        if args.video and args.auth == "chatgpt":
            from chatgpt_auth import ChatGPTAuth
            auth = ChatGPTAuth()
            access_token = auth.access_token()
            if not model:
                model = auth.models()[0][1]
        summary = run(video=args.video, scene_path=args.scene, out=args.out, model=model,
                      frames=args.frames, auth_mode=args.auth, access_token=access_token,
                      offset=tuple(args.offset))
        print(f"Created: {summary['zip']} ({summary['commands']} block commands)")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
