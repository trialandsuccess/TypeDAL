from distutils.core import setup

with open('README.md') as f:
    long_desc = f.read()

setup(
    name='TypeDal',
    version='0.6.1',
    description='Typing support for PyDAL',
    author='Robin van der Noord',
    author_email='contact@trialandsuccess.nl',
    url='https://github.com/trialandsuccess/TypeDAL',
    packages=['typedal'],
    long_description=long_desc,
    long_description_content_type="text/markdown",
    install_requires=["pydal"],
    python_requires='>3.10.0',
)
