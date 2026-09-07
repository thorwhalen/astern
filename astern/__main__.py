"""``astern`` command line: ``cw`` over the functions :mod:`astern.tools` exposes."""

import cw

from astern.tools import _dispatch_config, _dispatch_funcs


def main():
    raise SystemExit(cw.dispatch(_dispatch_funcs, config=_dispatch_config))


if __name__ == "__main__":
    main()
