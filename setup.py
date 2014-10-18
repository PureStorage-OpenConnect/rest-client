import purestorage

from setuptools import setup

setup(
    name="purestorage",
    version=purestorage.VERSION,
    description="Pure Storage FlashArray REST Client",
    keywords=["pure", "storage", "flasharray", "rest", "client"],
    url="",
    author="Pure Storage",
    author_email = "",
    license="BSD 2-Clause",
    packages=["purestorage"],
    install_requires=["requests"],
    tests_require=['mock'],
)
