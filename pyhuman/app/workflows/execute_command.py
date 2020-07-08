from app.utility.base_workflow import BaseWorkflow


def load(driver):
    return ExecuteCommand(driver=driver)


class ExecuteCommand(BaseWorkflow):

    def __init__(self, driver):
        super().__init__(name='ExecuteCommand', description='Execute Custom Commands', driver=driver)
