from abc import abstractmethod


class BaseWorkflow(object):

    __slots__ = ['name', 'description', 'driver']

    @abstractmethod
    def __init__(self, name, description, driver):
        self.name = name
        self.description = description
        self.driver = driver

    @abstractmethod
    def action(self):
        pass
