from setuptools import setup

readme = open('README.rst', 'r')
README_TEXT = readme.read()
readme.close()

setup(
    name="purestorage",
    version="1.11.1",
    description="Pure Storage FlashArray REST Client",
    keywords=["pure", "storage", "flasharray", "rest", "client"],
    url="https://github.com/purestorage/rest-client",
    download_url="https://github.com/purestorage/rest-client/archive/1.11.1.tar.gz"
    author="Pure Storage",
    author_email = "",
    license="BSD 2-Clause",
    packages=["purestorage"],
    install_requires=["requests"],
    tests_require=['mock'],
    long_description=README_TEXT,
)
