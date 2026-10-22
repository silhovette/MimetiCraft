"""Java block semantics and small, deterministic scene-planning helpers.

No vision model is run here. Unknown/preserved space is an exclusion mask, not air.
"""
from itertools import product

COLORS = ('white', 'orange', 'magenta', 'light_blue', 'yellow', 'lime', 'pink',
          'gray', 'light_gray', 'cyan', 'purple', 'blue', 'brown', 'green', 'red', 'black')
SPECIAL_PALETTE = ['light', 'barrier', 'structure_void'] + [
    f'{color}_stained_glass{suffix}' for color in COLORS for suffix in ('', '_pane')]
BOOL = {'true', 'false'}
HORIZONTAL = {'north', 'south', 'east', 'west'}
DIRECTIONS = {'north': (0, 0, -1), 'south': (0, 0, 1),
              'east': (1, 0, 0), 'west': (-1, 0, 0)}

BLOCK_KNOWLEDGE = """
SPECIAL BLOCK EVIDENCE AND SEMANTICS:
Use chronological before/after placement changes, held items/hotbar icons, block
names/text, edges/textures/thickness, multiple views, water motion/submersion and
observable collisions. Color alone is insufficient, especially with shaders.
air: invisible, no collision/light. Only action=clear explicitly clears confirmed
empty space. Unobserved space is unknown, NEVER an automatic air fill.
light: invisible, no ordinary solid collision, level=0..15, waterlogged=true/false.
Brightness alone is NOT evidence. Require placement/item evidence or an explicit
user instruction. Examples: minecraft:light[level=8],
minecraft:light[level=15,waterlogged=true]. Level 0 does not emit light.
barrier: invisible SOLID collision. Require item markers, placement/collision
evidence or explicit instructions, never infer it from transparency alone.
structure_void: invisible, no ordinary solid collision. action=place means the
actual block; action=preserve means leave the original world untouched, generating
NO placement or removal command there. These meanings are not interchangeable.
glass/stained_glass: transparent full collision blocks, with visible edges/texture;
not air, water or invisible barriers. glass_pane/stained_glass_pane: thin connected
panels, NOT full glass; north/south/east/west=true/false and waterlogged=true/false.
water: a fluid, not transparent solid. level=0 is a source; level=1..15 represents
flow/falling states. Use motion, surface height, refraction and immersion rather
than color. Distinguish standalone water from another block's waterlogged=true.
Plan a watertight bottom and sides before water/waterlogged blocks. Do not invent
barriers to contain it. If a container cannot be established, mark it uncertain.
The final camera image alone cannot distinguish air/light/barrier/structure_void.
For insufficient evidence use action=unknown, confidence=uncertain, block=null.
Every operation includes evidence.frames (actual provided video frame IDs), a
short observation, and evidence.hint (the exact supplied hint when applicable,
otherwise null). Hint-only decisions must explicitly say no direct video evidence;
hints do not turn absent visual evidence into an observation.
Use action=place for confirmed placement, clear only for deliberate air clearing,
preserve for retaining the existing world, unknown for uncertain observations.
Use replace=true only for a deliberate later replacement of an earlier special
block at the SAME coordinates. A waterlogged block occupies one cell: do not also
place independent water over that cell. Ops still execute in their stated plan order.
"""


def split_block(block):
    name = block.split('[', 1)[0].removeprefix('minecraft:')
    states = dict(pair.split('=') for pair in block.split('[', 1)[1][:-1].split(',')) if '[' in block else {}
    return name, states


def is_pane(name):
    return name == 'glass_pane' or name.endswith('_stained_glass_pane')


def state_rules(name):
    if name == 'light':
        return {'level': {str(i) for i in range(16)}, 'waterlogged': BOOL}
    if name == 'water':
        return {'level': {str(i) for i in range(16)}}
    if name == 'barrier':
        return {'waterlogged': BOOL}
    if is_pane(name) or name.endswith('_fence') or name == 'iron_bars':
        return {**{key: BOOL for key in HORIZONTAL}, 'waterlogged': BOOL}
    if name.endswith('_stairs'):
        return {'facing': HORIZONTAL, 'half': {'top', 'bottom'}, 'waterlogged': BOOL,
                'shape': {'straight', 'inner_left', 'inner_right', 'outer_left', 'outer_right'}}
    if name.endswith('_trapdoor'):
        return {'facing': HORIZONTAL, 'half': {'top', 'bottom'}, 'open': BOOL,
                'powered': BOOL, 'waterlogged': BOOL}
    if name.endswith('_door'):
        return {'facing': HORIZONTAL, 'half': {'upper', 'lower'}, 'open': BOOL,
                'powered': BOOL, 'hinge': {'left', 'right'}}
    if name.endswith('_slab'):
        return {'type': {'top', 'bottom', 'double'}, 'waterlogged': BOOL}
    if name.endswith(('_log', '_wood')):
        return {'axis': {'x', 'y', 'z'}}
    if name.endswith('_leaves'):
        return {'distance': {str(i) for i in range(1, 8)}, 'persistent': BOOL, 'waterlogged': BOOL}
    if name == 'lantern':
        return {'hanging': BOOL, 'waterlogged': BOOL}
    if name == 'end_rod':
        return {'facing': HORIZONTAL | {'up', 'down'}}
    if name == 'barrel':
        return {'facing': HORIZONTAL | {'up', 'down'}, 'open': BOOL}
    if name == 'grass_block':
        return {'snowy': BOOL}
    return {}


def classify_block(block):
    name, _ = split_block(block)
    if name in ('air', 'light', 'barrier', 'structure_void', 'water'):
        return name
    if is_pane(name):
        return 'glass_pane'
    if name in ('glass', 'tinted_glass') or name.endswith('_stained_glass'):
        return 'glass'
    return 'solid_or_decoration'


def has_water(block):
    name, states = split_block(block)
    return name == 'water' or states.get('waterlogged') == 'true'


def full_seal(block):
    """Conservative full-cube subset of the compiler's supported palette."""
    name, states = split_block(block)
    if name.endswith('_slab'):
        return states.get('type') == 'double'
    return (name not in {'air', 'light', 'structure_void', 'water', 'lantern', 'end_rod', 'iron_bars'}
            and not is_pane(name) and not name.endswith(('_stairs', '_door', '_trapdoor', '_fence', '_leaves')))


def canonical_block(block):
    name, states = split_block(block)
    defaults = {'light': {'level': '15', 'waterlogged': 'false'},
                'water': {'level': '0'}, 'barrier': {'waterlogged': 'false'}}.get(name, {})
    return name, tuple(sorted({**defaults, **states}.items()))


def cells(op):
    return product(*(range(a, b + 1) for a, b in zip(op['from'], op['to'])))


def intersect(first, second):
    return all(first['from'][i] <= second['to'][i] and second['from'][i] <= first['to'][i]
               for i in range(3))


def subtract(box, mask):
    if not intersect(box, mask):
        return [box]
    a, b = list(box['from']), list(box['to'])
    result = []
    for axis in range(3):
        lo, hi = max(a[axis], mask['from'][axis]), min(b[axis], mask['to'][axis])
        if a[axis] < lo:
            end = b.copy()
            end[axis] = lo - 1
            result.append({**box, 'from': a.copy(), 'to': end})
            a[axis] = lo
        if b[axis] > hi:
            start = a.copy()
            start[axis] = hi + 1
            result.append({**box, 'from': start, 'to': b.copy()})
            b[axis] = hi
    return result


def resolve_operations(ops, limit):
    masks = [op for op in ops if op['action'] in ('preserve', 'unknown') or op['confidence'] == 'uncertain']
    plan = []
    for op in ops:
        if op in masks:
            continue
        pieces = [op]
        for mask in masks:
            pieces = [part for piece in pieces for part in subtract(piece, mask)]
        for piece in pieces:
            kept = []
            for old in plan:
                protected = classify_block(old['block']) not in ('solid_or_decoration', 'air') or has_water(old['block'])
                if (intersect(old, piece) and protected and
                        canonical_block(old['block']) != canonical_block(piece['block']) and
                        not piece['replace']):
                    raise ValueError('Overlapping special blocks require replace=true for the intentional later replacement')
                kept.extend(subtract(old, piece))
            plan = kept + [piece]
            if len(plan) > limit:
                raise ValueError('Scene exclusions/overlaps produce too many command regions')
    return plan


def prepare_plan(ops, limit):
    plan = resolve_operations(ops, limit)
    world = {point: op['block'] for op in plan for point in cells(op)}
    resolved = []
    for op in plan:
        name, states = split_block(op['block'])
        if not is_pane(name) or HORIZONTAL <= states.keys():
            resolved.append(op)
            continue
        # Resolve missing pane connections from final planned geometry. Explicit
        # states win; unknown world neighbors are never assumed to be supports.
        runs = []
        for y in range(op['from'][1], op['to'][1] + 1):
            for z in range(op['from'][2], op['to'][2] + 1):
                for x in range(op['from'][0], op['to'][0] + 1):
                    connected = states.copy()
                    for direction, delta in DIRECTIONS.items():
                        neighbor = world.get((x + delta[0], y, z + delta[2]))
                        value = neighbor and (is_pane(split_block(neighbor)[0]) or full_seal(neighbor))
                        connected.setdefault(direction, 'true' if value else 'false')
                    block = 'minecraft:' + name + '[' + ','.join(f'{k}={v}' for k, v in sorted(connected.items())) + ']'
                    if runs and runs[-1]['block'] == block and runs[-1]['to'] == [x - 1, y, z]:
                        runs[-1]['to'][0] = x
                    else:
                        runs.append({**op, 'block': block, 'from': [x, y, z], 'to': [x, y, z]})
        resolved.extend(runs)
    if len(resolved) > limit:
        raise ValueError('Pane connections produce too many command regions')
    return resolved


def water_boundaries(plan):
    world = {point: op['block'] for op in plan for point in cells(op)}
    boundaries = set()
    for point, block in world.items():
        if not has_water(block):
            continue
        for delta in (*DIRECTIONS.values(), (0, -1, 0)):
            neighbor = tuple(point[i] + delta[i] for i in range(3))
            other = world.get(neighbor)
            if other is None:
                boundaries.add(neighbor)
            elif not (has_water(other) or full_seal(other)):
                raise ValueError(f'Unsealed water boundary at {neighbor}: {other}. Plan a watertight container first.')
    return sorted(boundaries)


def build_phase(op):
    if op['block'] == 'minecraft:air':
        return 0
    if has_water(op['block']):
        return 3
    if full_seal(op['block']):
        return 1
    return 2
