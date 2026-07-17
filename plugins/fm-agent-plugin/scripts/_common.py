from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fm_agent_core import state  # noqa: E402


def common_scope(parser):
    parser.add_argument("--project", required=True)
    parser.add_argument("--one-phase", action="store_true", default=None)
    parser.add_argument("--submodule", dest="submodules", action="append", default=[])
    parser.add_argument("--extra-edge")
    parser.add_argument("--knowledge", action="append", default=[])
    parser.add_argument("--isolate", action="store_true", default=None)


def project(args):
    return Path(args.project).expanduser().resolve()


def scope(args, config=None):
    return state.fingerprint(project(args), bool(args.one_phase), args.submodules, args.extra_edge, args.knowledge, config)
