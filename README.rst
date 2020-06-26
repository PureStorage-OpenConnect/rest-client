Pure Storage FlashArray REST 1.X SDK
====================================
This library is designed to provide a simple interface for issuing commands to
a Pure Storage FlashArray using a REST API. It communicates with the array
using the python requests HTTP library.


Requirements
============
This library requires the use of python 2.6 or later and the third-party
library "requests".

Additionally, this library can only be used communicate with FlashArrays that
support one or more REST API versions between 1.0 and 1.19; currently, this
includes any FlashArray running Purity 3.4.0 or later.


Capabilities
============
This library supports all functionality offered by FlashArray REST API versions from 1.0 up to 1.19.

Note that different versions of the REST API offer different functionality, and
some operations may be unusable except on certain versions of the REST API. For
example, functionality relating to FlashRecover and protection groups (pgroups)
requires the use of REST API version 1.2, which is supported only by Purity
versions 4.0 and later.


Installation
============
::

 $ python setup.py install


Documentation
=============

http://pure-storage-python-rest-client.readthedocs.io/en/stable/


Tests
=====
From the root directory of the rest-client
::

 $ PYTHONPATH=$(pwd):$PYTHONPATH py.test test/*.py


Files
=====
* purestorage/ -- Contains library code.
* docs/ -- Contains API documentation, Makefile and conf.py.
* CHANGES.rst -- Library change log.
* LICENSE.txt -- Library BSD 2-Clause license.
* README.txt -- This document.
