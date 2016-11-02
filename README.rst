Pure Storage REST Client
========================
This library is designed to provide a simple interface for issuing commands to
a Pure Storage Flash Array using a REST API. It communicates with the array
using the python requests HTTP library.


Requirements
============
This library requires the use of python 2.6 or later and the third-party
library "requests".

Additionally, this library can only be used communicate with Flash Arrays that
support one or more REST API versions between 1.0 and 1.8; currently, this
includes any Flash Array running Purity 3.4.0 or later.


Capabilities
============
This library supports all functionality offered by REST API versions up to 1.8.

Note that different versions of the REST API offer different functionality, and
some operations may be unusable except on certain versions of the REST API. For
example, functionality relating to FlashRecover and protection groups (pgroups)
requires the use of REST API version 1.2, which is supported only by Purity
versions 4.0 and later.


Installation
============
::

 $ python setup.py install


Tests
=====
From the root directory of the rest-client
::

 $ PYTHONPATH=$(pwd):$PYTHONPATH py.test test/\*.py


Files
=====
* purestorage/ -- Contains library code.
* docs/ -- Contains API documentation, Makefile and conf.py.
* docs/changelog.rst -- Library change log.
* test/ -- Contains tests for this library.
* LICENSE.txt -- Library BSD 2-Clause license.
* README.rst -- This document.
