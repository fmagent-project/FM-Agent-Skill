#!/usr/bin/env python3
import argparse
from _common import project, state

parser = argparse.ArgumentParser(description="Create a restricted local incremental intent artifact.")
parser.add_argument("--project", required=True)
parser.add_argument("--base", required=True)
parser.add_argument("--note", default="")
parser.add_argument("--run-id")
args = parser.parse_args()
print(state.build_intent(project(args), args.base, args.note, args.run_id))
