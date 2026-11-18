"""Validate a finished development episode against its saved evidence."""

import argparse
import json

from dojo_astra.episode_validation import validate_episode

parser = argparse.ArgumentParser()
parser.add_argument("output")
args = parser.parse_args()
print(json.dumps(validate_episode(args.output)))
