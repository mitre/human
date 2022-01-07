import subprocess

from ..utility.base_workflow import BaseWorkflow


def load():
    return ExecuteCommand()


class ExecuteCommand(BaseWorkflow):

    def __init__(self):
        super().__init__(name='ExecuteCommand', description='Execute Custom Commands')

    @staticmethod
    def action(extra=None):
        for c in extra:
            subprocess.Popen(c, shell=True)
