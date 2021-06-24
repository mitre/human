from abc import abstractmethod


class BaseWorkflow(object):

    @property
    def display(self):
        return 'Running Task: {}'.format(self.description)

    __slots__ = ['name', 'description', 'driver']

    @abstractmethod
    def __init__(self, name, description, driver):
        self.name = name
        self.description = description
        self.driver = driver

    @abstractmethod
    def action(self, extra=None):
        pass
