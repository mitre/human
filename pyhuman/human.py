import argparse
import signal
import os
import random
import sys
from importlib import import_module
from time import sleep


TASK_CLUSTER_COUNT = 5
TASK_INTERVAL_SECONDS = 10
GROUPING_INTERVAL_SECONDS = 500
EXTRA_DEFAULTS = []


def emulation_loop(workflows, clustersize, taskinterval, taskgroupinterval, extra):
    while True:
        for c in range(clustersize):
            sleep(random.randrange(taskinterval))
            index = random.randrange(len(workflows))
            print(workflows[index].display)
            workflows[index].action(extra)
        sleep(random.randrange(taskgroupinterval))


def import_workflows():
    extensions = []
    for root, dirs, files in os.walk(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'app', 'workflows')):
        files = [f for f in files if not f[0] == '.' and not f[0] == "_"]
        dirs[:] = [d for d in dirs if not d[0] == '.' and not d[0] == "_"]
        for file in files:
            try:
                extensions.append(load_module('app/workflows', file))
            except Exception as e:
                print('Error could not load workflow. {}'.format(e))
    return extensions


def load_module(root, file):
    module = os.path.join(*root.split('/'), file.split('.')[0]).replace(os.path.sep, '.')
    workflow_module = import_module(module)
    return getattr(workflow_module, 'load')()


def run(clustersize, taskinterval, taskgroupinterval, extra):
    random.seed()
    workflows = import_workflows()

    def signal_handler(sig, frame):
        for workflow in workflows:
            workflow.cleanup()
        exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    emulation_loop(workflows=workflows, clustersize=clustersize, taskinterval=taskinterval,
                    taskgroupinterval=taskgroupinterval, extra=extra)


def run_control(sock_path=None):
    """Boot the keep-alive control server (Timestone mode).

    Loads workflows the same way the random scheduler does, then hands off to
    control_server.serve(), which blocks waiting for operator-pushed jobs over
    a Unix domain socket.
    """
    # Support both `python -m pyhuman.human` (package context) and
    # `python pyhuman/human.py` (script context).
    try:
        from . import control_server  # type: ignore
    except ImportError:
        here = os.path.dirname(os.path.realpath(__file__))
        parent = os.path.dirname(here)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        from pyhuman import control_server  # type: ignore

    workflows = import_workflows()

    def signal_handler(sig, frame):
        for workflow in workflows:
            try:
                workflow.cleanup()
            except Exception:  # noqa: BLE001
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    control_server.serve(workflows, sock_path=sock_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Emulate human behavior on a system')
    parser.add_argument('--mode', choices=['random', 'control'], default='random',
                        help='random = legacy random-walk emulation loop; '
                             'control = keep-alive command server (UDS).')
    parser.add_argument('--sock', default=None,
                        help='UDS path for --mode control '
                             '(defaults to $PYHUMAN_CONTROL_SOCK or /tmp/pyhuman.sock).')
    parser.add_argument('--clustersize', type=int, default=TASK_CLUSTER_COUNT)
    parser.add_argument('--taskinterval', type=int, default=TASK_INTERVAL_SECONDS)
    parser.add_argument('--taskgroupinterval', type=int, default=GROUPING_INTERVAL_SECONDS)
    parser.add_argument('--extra', nargs='*', default=EXTRA_DEFAULTS)
    args = parser.parse_args()

    try:
        if args.mode == 'control':
            run_control(sock_path=args.sock)
        else:
            run(
                clustersize=args.clustersize,
                taskinterval=args.taskinterval,
                taskgroupinterval=args.taskgroupinterval,
                extra=args.extra
            )
    except KeyboardInterrupt:
        print(" Terminating human execution...")
        sys.exit()
