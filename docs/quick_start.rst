Quick Start Guide
=================

This guide is intended to give users a basic idea of REST Client usage
through examples.


Before You Begin
----------------

You should already have the Pure Storage FlashArray REST Client installed.

This includes installing REST Client package dependencies.

See :doc:`installation` for more information.


Starting A Session
------------------

To verify the REST Client package is installed and importable, try executing
the following in a Python interpreter:

.. code-block:: python

    >>> import purestorage


If that succeeds without an ImportError, you are ready to start a REST session
using the client.

REST sessions are automatically established when a FlashArray object is
instantiated. To instantiate a FlashArray object, provide the IP address
or domain name of the target array as well as a username and password, or
API token.

.. code-block:: python

    >>> array = purestorage.FlashArray("localhost", "pureuser", "pureuser")


.. code-block:: python

    >>> array = purestorage.FlashArray("localhost",
            api_token="6e1b80a1-cd63-de90-b74a-7fc16d034016")


.. code-block:: python

    from purestorage import FlashArray

    array = FlashArray("localhost", "pureuser", "pureuser")
    array_info = array.get()
    print "FlashArray {} (version {}) REST session established!".format(
            array_info['array_name'], array_info['version'])


To end a session, invalidate your REST cookie:

.. code-block:: python

    array.invalidate_cookie()


Calling any other methods again creates a new cookie automatically.


Creating Volumes
----------------

When creating a volume, specify the volume name and a size.

Size can either be an integer or a string with an optional suffix.

.. code-block:: python

        >>> array.create_volume("vol1", 1024 ** 3)
        {
         u'source': None,
         u'serial': u'DABA29111570F7A4000114C0',
         u'size': 1073741824,
         u'name': u'vol1',
         u'created': u'2014-08-11T17:19:35Z'
        }
        >>> array.create_volume("vol2", "5M")
        {
         u'source': None,
         u'serial': u'DABA29111570F7A4000114C1',
         u'size': 524288000,
         u'name': u'vol2',
         u'created': u'2014-08-11T17:19:51Z'
        }


Creating Hosts and Hgroups
--------------------------

Host creation requires a name only.

Optionally IQNs or WWNs can be specified during creation, or they can
be set for a particular host after creating.

Similarly, hgroup creation requires a name only and hosts can be
added to the hgroup as part of creation or in a subsequent set call.

.. code-block:: python

        >>> array.create_host("host1", iqnlist=["iqn.2001-04.com.example:diskarrays-sn-a8675308",
                                                "iqn.2001-04.com.example:diskarrays-sn-a8675309"])
        {
         u'iqn': [u'iqn.2001-04.com.example:diskarrays-sn-a8675308', u'iqn.2001-04.com.example:diskarrays-sn-a8675309'],
         u'wwn': [],
         u'name': u'host1'
        }
        >>> array.create_host("host2")
        {
         u'iqn': [],
         u'wwn': [],
         u'name':
         u'host2'
        }
        >>> array.set_host("host2", wwnlist=["1234567812345678"])
        {
         u'iqn': [],
         u'wwn': [u'1234567812345678'],
         u'name': u'host2',
         u'hgroup': None
        }
        >>> array.create_hgroup("hgroup1", hostlist=["host1", "host2"])
        {
         u'hosts': [u'host1', u'host2'],
         u'name': u'hgroup1'
        }


Connecting Volumes
------------------

When connecting volumes to hosts and hgroups, just specify the volume name
and the name of the host or hgroup. LUNs may also be specified as optional
keyword arguments.

.. code-block:: python

        >>> array.connect_host("host1", "vol1")
        {
         u'vol': u'vol1',
         u'name': u'host1',
         u'lun': 1
        }
        >>> array.connect_hgroup("hgroup1", "vol2")
        {
         u'vol': u'vol2',
         u'name': u'hgroup1',
         u'lun': 10
        }
        >>> array.list_host_connections("host1")
        [
         {
          u'vol': u'vol1',
          u'name': u'host1',
          u'lun': 1,
          u'hgroup': None
         },
         {
          u'vol': u'vol2',
          u'name': u'host1',
          u'lun': 10,
          u'hgroup': u'hgroup1'
         }
        ]
        >>> array.list_hgroup_connections("hgroup1")
        [{
          u'vol': u'vol2',
          u'name': u'hgroup1',
          u'lun': 10
        }]


Using Snapshots
---------------

Snapshots can be taken of individual volumes or collections of volumes. Snapshots
of more than one volume are guaranteed to be point in time consistent.

Snapshots (or volumes) can be copied out to new volumes.

.. code-block:: python

        >>> array.create_snapshot("vol2")
        {
         u'source': u'vol2',
         u'serial': u'DABA29111570F7A4000115A3',
         u'size': 5242880,
         u'name': u'vol2.5539',
         u'created': u'2014-08-15T17:21:22Z'
        }
        >>> array.create_snapshots(["vol1", "vol2"], suffix="together")
        [
         {
          u'source': u'vol1',
          u'serial': u'DABA29111570F7A4000115A4',
          u'size': 1073741824,
          u'name': u'vol1.together',
          u'created': u'2014-08-15T17:21:58Z'
         },
         {
          u'source': u'vol2',
          u'serial': u'DABA29111570F7A4000115A5',
          u'size': 5242880,
          u'name': u'vol2.together',
          u'created': u'2014-08-15T17:21:58Z'
         }
        ]
        >>> array.copy_volume("vol1.together", "vol3")
        {
         u'source': u'vol1',
         u'serial': u'DABA29111570F7A4000115A6',
         u'size': 1073741824,
         u'name': u'vol3',
         u'created': u'2014-08-15T17:21:58Z'
        }
        >>> array.list_volumes(snap=True)
        [
         {
          u'source': u'vol1',
          u'serial': u'DABA29111570F7A4000115A4',
          u'size': 1073741824,
          u'name': u'vol1.together',
          u'created': u'2014-08-15T17:21:58Z'
         },
         {
          u'source': u'vol2',
          u'serial': u'DABA29111570F7A4000115A5',
          u'size': 5242880,
          u'name': u'vol2.together',
          u'created': u'2014-08-15T17:21:58Z'
         },
         {
          u'source': u'vol2',
          u'serial': u'DABA29111570F7A4000115A3',
          u'size': 5242880,
          u'name': u'vol2.5539',
          u'created': u'2014-08-15T17:21:22Z'
         }
        ]


Disconnecting and Destroying Volumes
------------------------------------

Volumes must be disconnected before they can be destroyed, just as hosts must
be disconnected before they can be deleted.

A destroyed volume may be recovered (for up to 24 hours following destruction)
or explicitly eradicated.

.. code-block:: python

        >>> array.disconnect_host("host1", "vol1")
        {
         u'vol': u'vol1',
         u'name': u'host1'
        }
        >>> array.destroy_volume("vol1")
        {
         u'name': u'vol1'
        }
        >>> array.list_volumes(pending_only=True)
        [
         {
          u'name': u'vol1',
          u'created': u'2014-08-15T17:13:08Z',
          u'source': None,
          u'time_remaining': 86400,
          u'serial': u'DABA29111570F7A4000115A1',
          u'size': 1073741824
         }
        ]
        >>> array.recover_volume("vol1")
        {
         u'name': u'vol1'
        }
        >>> array.rename_volume("vol1", "renamed")
        {
         u'name': u'renamed'
        }
        >>> array.destroy_volume("renamed")
        {
         u'name': u'renamed'
        }
        >>> array.eradicate_volume("renamed")
        {
         u'name': u'renamed'
        }
        >>> array.list_volumes(pending_only=True)
        []


Enable Secure HTTPS Requests
----------------------------

By default the requests being made will not verify the SSL certificate of the
target array. Requests made this way will log a InsecureRequestWarning.

To enable verification use the verify_https flag:

.. code-block:: python

    >>> array = purestorage.FlashArray("localhost", "pureuser", "pureuser", verify_https=True)


This does require that the target array has a trusted certificate and will
be validated correctly by the system making the request.

If using an 'untrusted' certificate (e.g. self-signed certificate) you can
optionally pass in a path to the certificate file:

.. code-block:: python

    >>> array = purestorage.FlashArray("localhost", "pureuser", "pureuser", verify_https=True,
                                       ssl_cert="/etc/ssl/certs/pure-self-signed.crt")

