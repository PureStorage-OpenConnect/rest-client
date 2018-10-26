from setuptools import setup

readme = open('README.rst', 'r')
README_TEXT = readme.read()
readme.close()

setup(
    name="purestorage",
    version="1.16.0",
    description="Pure Storage FlashArray REST Client",
    keywords=["pure", "storage", "flasharray", "rest", "client"],
    url="https://github.com/purestorage/rest-client",
    download_url="https://github.com/purestorage/rest-client/archive/1.16.0.tar.gz",
    author="Pure Storage",
    author_email = "wes@purestorage.com",
    license="BSD 2-Clause",
    packages=["purestorage"],
    install_requires=["requests"],
    tests_require=['mock'],
    long_description=README_TEXT,
)
