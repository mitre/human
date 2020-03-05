from setuptools import setup, find_packages

setup(
    name='pyhuman',
    version='0.1',
    author='MITRE',
    author_email='caldera@mitre.org',
    description='Package to emulate human behaviors on an endpoint',
    packages=find_packages(),
    python_requires='>=3.6.1'
)