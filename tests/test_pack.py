import json
import tempfile
from pathlib import Path
import unittest
import zipfile

from video2pack import compile_datapack, validate_scene, split_fill, make_undo_commands

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "data/vidbuild/function/"

class PackTests(unittest.TestCase):
    def load_demo(self):
        return json.loads((ROOT / "examples" / "demo_house.json").read_text())

    def test_structure_and_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "build.zip"
            info = compile_datapack(self.load_demo(), out)
            self.assertGreater(info["commands"], 5)
            self.assertGreater(info["undo_commands"], 5)
            with zipfile.ZipFile(out) as z:
                self.assertIsNone(z.testzip())
                names = set(z.namelist())
                for name in ["build", "undo", "check", "internal/start", "internal/build_at_origin",
                             "internal/undo_from_saved_origin", "internal/undo_at_origin",
                             "internal/commit_undo", "parts/all", "parts/undo_all"]:
                    self.assertIn(PREFIX + name + ".mcfunction", names)
                self.assertEqual(json.loads(z.read("pack.mcmeta"))["pack"]["min_format"], [121,0])
                start = z.read(PREFIX + "internal/start.mcfunction").decode()
                self.assertIn("align xyz positioned ~5 ~0 ~5 summon minecraft:marker", start)
                capture = z.read(PREFIX + "internal/build_at_origin.mcfunction").decode()
                self.assertIn("last.x set from entity @s Pos[0]", capture)
                undo = z.read(PREFIX + "undo.mcfunction").decode()
                self.assertIn("with storage vidbuild:history last", undo)
                macro = z.read(PREFIX + "internal/undo_from_saved_origin.mcfunction").decode()
                self.assertIn("$execute in $(dimension) positioned $(x) $(y) $(z)", macro)
                self.assertIn("data remove storage vidbuild:history last", z.read(PREFIX + "internal/commit_undo.mcfunction").decode())
                for path in names:
                    if path.endswith(".mcfunction"):
                        for line in z.read(path).decode().splitlines():
                            self.assertFalse(line.startswith("/"), (path,line))
                undo_part = z.read(PREFIX + "parts/undo_part_000.mcfunction").decode()
                self.assertIn("minecraft:air replace minecraft:polished_andesite", undo_part)
                self.assertNotIn("minecraft:air replace minecraft:air", undo_part)

    def test_large_fill_split(self):
        cuboids = list(split_fill((0,0,0),(63,47,63)))
        self.assertTrue(all((b[0]-a[0]+1)*(b[1]-a[1]+1)*(b[2]-a[2]+1)<=4096 for a,b in cuboids))

    def test_undo_filters_and_does_not_clear_full_volume(self):
        scene = {"name":"tiny", "size":[4,4,4], "ops":[
            {"block":"minecraft:oak_stairs[facing=north]","from":[0,0,0],"to":[0,0,0]},
            {"block":"minecraft:glass","from":[2,2,2],"to":[3,2,2]},
            {"block":"minecraft:air","from":[1,1,1],"to":[1,1,1]},
        ]}
        lines = make_undo_commands(validate_scene(scene))
        self.assertEqual(len(lines), 2)
        self.assertIn("minecraft:air replace minecraft:glass", lines[0])
        self.assertIn("if block ~0 ~0 ~0 minecraft:oak_stairs", lines[1])

    def test_reject_scene_out_of_bounds(self):
        invalid={"name":"bad","size":[2,2,2],"ops":[{"block":"minecraft:stone","from":[0,0,0],"to":[8,0,0]}]}
        with self.assertRaises(ValueError):
            validate_scene(invalid)

if __name__=='__main__':
    unittest.main()
