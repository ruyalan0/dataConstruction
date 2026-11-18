"""Build a private asset view, resolving only official template paths."""

import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("destination", type=Path)
args = parser.parse_args()
args.destination.mkdir(parents=True, exist_ok=False)
for source in args.source.iterdir():
    target = args.destination / source.name
    if source.name != "Robots":
        target.symlink_to(source.resolve(), target_is_directory=source.is_dir())
        continue
    target.mkdir()
    for robot in source.iterdir():
        if robot.name != "x5":
            (target / robot.name).symlink_to(robot.resolve(), target_is_directory=robot.is_dir())
            continue
        dest_robot = target / robot.name
        dest_robot.mkdir()
        for item in robot.iterdir():
            (dest_robot / item.name).symlink_to(item.resolve(), target_is_directory=item.is_dir())
        template = (robot / "curobo_tmp.yml").read_text()
        template = template.replace("${ASSETS_PATH}/Assets", str(args.destination.resolve()))
        if "${" in template:
            raise ValueError("Unresolved official template variable")
        (dest_robot / "curobo.yml").write_text(template)
