from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

# get version from __version__ variable in spokes_sgt_app/__init__.py
from spokes_sgt_app import __version__ as version

setup(
	name="spokes_sgt_app",
	version=version,
	description="spokes_sgt_app",
	author="SGT",
	author_email="karantkiruba@hotmail.com",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)
