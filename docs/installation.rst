Installation Guide
==================

The Pure Storage FlashArray REST Client is available through the
Python Package Index.

The code is available on github and can optionally be built and
installed from source.


Python Package Index Installation
---------------------------------

.. code-block:: bash

    $ pip install purestorage

Or

.. code-block:: bash

    $ easy_install purestorage


Source Code Installation
------------------------

.. code-block:: bash

    $ mkdir purestorage
    $ cd purestorage
    $ git clone https://github.com/purestorage/rest-client.git
    $ cd rest-client
    $ python setup.py install

Or to build HTML documentation from source:

.. code-block:: bash

    $ cd docs/
    $ make html

This creates a _build/ directory under docs/.
