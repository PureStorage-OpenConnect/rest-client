"""
This library provides an easy way to script administration tasks for the
Pure Storage FlashArray.

When passing arguments to methods that take \*\*kwargs, the exact
parameters that can be passed can be found in the REST API guide for the
given release of Purity running on the FlashArray.
"""

import json
import requests

from distutils.version import LooseVersion

# The current version of this library.
VERSION = "1.16.0"


class FlashArray(object):

    """Represents a Pure Storage FlashArray and exposes administrative APIs.

    :param target: IP address or domain name of the target array's management
                   interface.
    :type target: str
    :param username: Username of the user with which to log in.
    :type username: str, optional
    :param password: Password of the user with which to log in.
    :type password: str, optional
    :param api_token: API token of the user with which to log in.
    :type api_token: str, optional
    :param rest_version: REST API version to use when communicating with
                         target array.
    :type rest_version: str, optional
    :param verify_https: Enable SSL certificate verification for HTTPS requests.
    :type verify_https: bool, optional
    :param ssl_cert: Path to SSL certificate or CA Bundle file. Ignored if
                     verify_https=False.
    :type ssl_cert: str, optional
    :param user_agent: String to be used as the HTTP User-Agent for requests.
    :type user_agent: str, optional
    :param request_kwargs: Keyword arguments that we will pass into the the call
                           to requests.request.
    :type request_kwargs: dict, optional

    :raises: :class:`PureError`

        - If the target array cannot be found.
        - If the target array does not support any of the REST versions used by
          this library.
        - If the username and password or api_token are invalid.

    :raises: :class:`ValueError`

        - If no api_token or username and password are specified.
        - If an api_token and a username or password are specified.
        - If the specified rest_version is not supported by this library or by
          the target array.

    .. note::

        The FlashArray constructor requires either a username and password or
        an api_token but not both.

    .. note::

        If a rest_version is not specified, the FlashArray object uses the
        highest REST API version supported by both the target array and this
        library. If the REST API version should become deprecated during the
        lifetime of the FlashArray object, the object renegotiates a REST
        version to use and continues running.

    .. note::

        If a rest_version is specified, that version is used so long as it is
        supported by both the target array and this library. In this case, the
        FlashArray object does not attempt to renegotiate the REST API version.

    .. note::

        Valid entries in request_kwargs may vary by your version of requests.

        If you wish to use secure connections, we suggest you use an entry in
        request_kwargs rather than the verify_https and ssl_cert arguments.
        (e.g. request_kwargs={"verify": "path/to/ca_bundle"})
        You should consider these options deprecated, though we will continue
        to support them for backward compatibility for the foreseeable future.

    """

    supported_rest_versions = [
            "1.16",
            "1.15",
            "1.14",
            "1.13",
            "1.12",
            "1.11",
            "1.10",
            "1.9",
            "1.8",
            "1.7",
            "1.6",
            "1.5",
            "1.4",
            "1.3",
            "1.2",
            "1.1",
            "1.0",
        ]

    def __init__(self, target, username=None, password=None, api_token=None,
                 rest_version=None, verify_https=False, ssl_cert=None,
                 user_agent=None, request_kwargs=None):

        if not api_token and not (username and password):
            raise ValueError(
                "Must specify API token or both username and password.")
        elif api_token and (username or password):
            raise ValueError(
                "Specify only API token or both username and password.")

        self._cookies = {}
        self._target = target

        self._renegotiate_rest_version = False if rest_version else True

        self._request_kwargs = dict(request_kwargs or {})
        if not "verify" in self._request_kwargs:
            if ssl_cert and verify_https:
                self._request_kwargs["verify"] = ssl_cert
            else:
                self._request_kwargs["verify"] = verify_https

        self._user_agent = user_agent

        self._rest_version = rest_version
        if self._rest_version:
            self._rest_version = self._check_rest_version(rest_version)
        else:
            self._rest_version = self._choose_rest_version()

        self._api_token = (api_token or self._obtain_api_token(username, password))
        self._start_session()

    def _format_path(self, path):
        return "https://{0}/api/{1}/{2}".format(
                self._target, self._rest_version, path)

    def _request(self, method, path, data=None, reestablish_session=True):
        """Perform HTTP request for REST API."""
        if path.startswith("http"):
            url = path  # For cases where URL of different form is needed.
        else:
            url = self._format_path(path)

        headers = {"Content-Type": "application/json"}
        if self._user_agent:
            headers['User-Agent'] = self._user_agent

        body = json.dumps(data).encode("utf-8")
        try:
            response = requests.request(method, url, data=body, headers=headers,
                                        cookies=self._cookies, **self._request_kwargs)
        except requests.exceptions.RequestException as err:
            # error outside scope of HTTP status codes
            # e.g. unable to resolve domain name
            raise PureError(err.message)

        if response.status_code == 200:
            if "application/json" in response.headers.get("Content-Type", ""):
                if response.cookies:
                    self._cookies.update(response.cookies)
                else:
                    self._cookies.clear()
                content = response.json()
                if isinstance(content, list):
                    content = ResponseList(content)
                elif isinstance(content, dict):
                    content = ResponseDict(content)
                content.headers = response.headers
                return content
            raise PureError("Response not in JSON: " + response.text)
        elif response.status_code == 401 and reestablish_session:
            self._start_session()
            return self._request(method, path, data, False)
        elif response.status_code == 450 and self._renegotiate_rest_version:
            # Purity REST API version is incompatible.
            old_version = self._rest_version
            self._rest_version = self._choose_rest_version()
            if old_version == self._rest_version:
                # Got 450 error, but the rest version was supported
                # Something really unexpected happened.
                raise PureHTTPError(self._target, str(self._rest_version), response)
            return self._request(method, path, data, reestablish_session)
        else:
            raise PureHTTPError(self._target, str(self._rest_version), response)

    #
    # REST API session management methods
    #

    def _check_rest_version(self, version):
        """Validate a REST API version is supported by the library and target array."""
        version = str(version)

        if version not in self.supported_rest_versions:
            msg = "Library is incompatible with REST API version {0}"
            raise ValueError(msg.format(version))

        array_rest_versions = self._list_available_rest_versions()
        if version not in array_rest_versions:
            msg = "Array is incompatible with REST API version {0}"
            raise ValueError(msg.format(version))

        return LooseVersion(version)

    def _choose_rest_version(self):
        """Return the newest REST API version supported by target array."""
        versions = self._list_available_rest_versions()
        versions = [LooseVersion(x) for x in versions if x in self.supported_rest_versions]
        if versions:
            return max(versions)
        else:
            raise PureError(
                "Library is incompatible with all REST API versions supported"
                "by the target array.")

    def _list_available_rest_versions(self):
        """Return a list of the REST API versions supported by the array"""
        url = "https://{0}/api/api_version".format(self._target)

        data = self._request("GET", url, reestablish_session=False)
        return data["version"]

    def _obtain_api_token(self, username, password):
        """Use username and password to obtain and return an API token."""
        data = self._request("POST", "auth/apitoken",
                             {"username": username, "password": password},
                             reestablish_session=False)
        return data["api_token"]

    def _start_session(self):
        """Start a REST API session."""
        self._request("POST", "auth/session", {"api_token": self._api_token},
                      reestablish_session=False)

    def get_rest_version(self):
        """Get the REST API version being used by this object.

        :returns: The REST API version.
        :rtype: str

        """
        return str(self._rest_version)

    def invalidate_cookie(self):
        """End the REST API session by invalidating the current session cookie.

        .. note::
            Calling any other methods again creates a new cookie. This method
            is intended to be called when the FlashArray object is no longer
            needed.

        """
        self._request("DELETE", "auth/session")

    #
    # Array management methods
    #

    def _set_console_lock(self, **kwargs):
        return self._request("PUT", "array/console_lock", kwargs)

    def enable_console_lock(self):
        """Enable root lockout from the array at the physical console.

        :returns: A dictionary mapping "console_lock" to "enabled".
        :rtype: ResponseDict

        """
        return self._set_console_lock(enabled=True)

    def disable_console_lock(self):
        """Disable root lockout from the array at the physical console.

        :returns: A dictionary mapping "console_lock" to "disabled".
        :rtype: ResponseDict

        """
        return self._set_console_lock(enabled=False)

    def get(self, **kwargs):
        """Get array attributes.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET array**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the array or a list of dictionaries
                  describing multiple array attributes, depending on the
                  arguments passed in.
        :rtype: ResponseDict or ResponseList

        """
        return self._request("GET", "array", kwargs)

    def get_console_lock_status(self):
        """Get console-lock status of the array.

        :returns: A dictionary mapping "console_lock" to "enabled" if
                  console_lock is enabled, else "disabled".
        :rtype: ResponseDict

        """
        return self._request("GET", "array/console_lock")

    def rename(self, name):
        """Rename the array.

        :param name: The new name for the array.
        :param type: str

        :returns: A dictionary mapping "array_name" to name.
        :rtype: ResponseDict

        """
        return self.set(name=name)

    def set(self, **kwargs):
        """Set array attributes.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT array**
        :type \*\*kwargs: optional

        :returns: A dictionary mapping the parameter that was set to its
                  new value.
        :rtype: ResponseDict

        """
        return self._request("PUT", "array", kwargs)

    #
    # Volume and snapshot management methods
    #

    def set_volume(self, volume, **kwargs):
        """Perform actions on a volume and return a dictionary describing it.

        :param volume: Name of the volume to be modified.
        :type volume: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT volume/:volume**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the created volume.
        :rtype: ResponseDict

        """
        return self._request("PUT", "volume/{0}".format(volume), kwargs)

    def create_snapshot(self, volume, **kwargs):
        """Create a snapshot of the given volume.

        :param volume: Name of the volume of which to take a snapshot.
        :type volume: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST volume**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the new snapshot.
        :rtype: ResponseDict

        """
        return self.create_snapshots([volume], **kwargs)[0]

    def create_snapshots(self, volumes, **kwargs):
        """Create snapshots of the listed volumes.

        :param volumes: List of names of the volumes to snapshot.
        :type volumes: list of str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST volume**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing the new snapshots.
        :rtype: ResponseDict

        """
        data = {"source": volumes, "snap": True}
        data.update(kwargs)
        return self._request("POST", "volume", data)

    def create_volume(self, volume, size, **kwargs):
        """Create a volume and return a dictionary describing it.

        :param volume: Name of the volume to be created.
        :type volume: str
        :param size: Size in bytes, or string representing the size of the
                     volume to be created.
        :type size: int or str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST volume/:volume**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the created volume.
        :rtype: ResponseDict

        .. note::

           The maximum volume size supported is 4 petabytes (4 * 2^50).

        .. note::

           If size is an int, it must be a multiple of 512.

        .. note::

           If size is a string, it  must consist of an integer followed by a
           valid suffix.

        Accepted Suffixes

        ====== ======== ======
        Suffix Size     Bytes
        ====== ======== ======
        S      Sector   (2^9)
        K      Kilobyte (2^10)
        M      Megabyte (2^20)
        G      Gigabyte (2^30)
        T      Terabyte (2^40)
        P      Petabyte (2^50)
        ====== ======== ======

        """
        data = {"size": size}
        data.update(kwargs)
        return self._request("POST", "volume/{0}".format(volume), data)

    def create_conglomerate_volume(self, volume):
        """Create a conglomerate volume and return a dictionary describing it.

        :param volume: Name of the volume to be created.
        :type volume: str

        :returns: A dictionary describing the created conglomerate volume.
        :rtype: ResponseDict

        .. note::

           This is not a typical volume thus there is no size.  It's main purpose to connect to a
           host/hgroup to create a PE LUN.  Once the conglomerate volume is connected to a
           host/hgroup, it is used as a protocol-endpoint to connect a vvol to a host/hgroup to
           allow traffic.

        .. note::

            Requires use of REST API 1.13 or later.

        """
        return self._request("POST", "volume/{0}".format(volume), {"protocol_endpoint": True})

    def copy_volume(self, source, dest, **kwargs):
        """Clone a volume and return a dictionary describing the new volume.

        :param source: Name of the source volume.
        :type source: str
        :param dest: Name of the destination volume.
        :type dest: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST volume/:volume**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the destination volume.
        :rtype: ResponseDict

        """
        data = {"source": source}
        data.update(kwargs)
        return self._request("POST", "volume/{0}".format(dest), data)

    def destroy_volume(self, volume):
        """Destroy an existing volume or snapshot.

        :param volume: Name of the volume to be destroyed.
        :type volume: str

        :returns: A dictionary mapping "name" to volume.
        :rtype: ResponseDict

        .. warnings also::

            This operation may cause a loss of data. The destroyed volume can
            be recovered during the 24 hours immediately following its
            destruction unless it is eradicated before then.

        """
        return self._request("DELETE", "volume/{0}".format(volume))

    def eradicate_volume(self, volume):
        """Eradicate a destroyed volume or snapshot.

        :param volume: Name of the volume to be eradicated.
        :type volume: str

        :returns: A dictionary mapping "name" to volume.
        :rtype: ResponseDict

        .. note::

            This operation fails if volume is not destroyed.

        .. warnings also::

            This operation may permanently erase data and the volume cannot
            be recovered.

        """
        return self._request("DELETE", "volume/{0}".format(volume),
                             {"eradicate": True})

    def extend_volume(self, volume, size):
        """Extend a volume to a new, larger size.

        :param volume: Name of the volume to be extended.
        :type volume: str
        :type size: int or str
        :param size: Size in bytes, or string representing the size of the
                     volume to be created.

        :returns: A dictionary mapping "name" to volume and "size" to the volume's
                  new size in bytes.
        :rtype: ResponseDict

        .. note::

            The new size must be larger than the volume's old size.

        .. note::

            The maximum volume size supported is 4 petabytes (4 * 2^50).

        .. note::

            If size is an int, it must be a multiple of 512.

        .. note::

           If size is a string, it  must consist of an integer followed by a
           valid suffix.

        Accepted Suffixes

        ====== ======== ======
        Suffix Size     Bytes
        ====== ======== ======
        S      Sector   (2^9)
        K      Kilobyte (2^10)
        M      Megabyte (2^20)
        G      Gigabyte (2^30)
        T      Terabyte (2^40)
        P      Petabyte (2^50)
        ====== ======== ======

        """
        return self.set_volume(volume, size=size, truncate=False)

    def get_volume(self, volume, **kwargs):
        """Return a dictionary describing a volume or snapshot.

        :param volume: Name of the volume to get information about.
        :type volume: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET volume/:volume**
        :type \*\*kwargs: optional

        :returns: A list describing snapshots of the volume if the paramater
                  snap is passed as True, else a dictionary describing the
                  volume.
        :rtype: ResponseDict or ResponseList

        """
        return self._request("GET", "volume/{0}".format(volume), kwargs)

    def add_volume(self, volume, pgroup):
        """Add a volume to a pgroup.

        :param volume: Name of the volume to add to pgroup.
        :type volume: str
        :param pgroup: pgroup to which to add volume.
        :type pgroup: str

        :returns: A dictionary mapping "name" to volume and "protection_group"
                  to pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.7 or later.

        """
        return self._request("POST", "volume/{0}/pgroup/{1}".format(volume, pgroup))

    def remove_volume(self, volume, pgroup):
        """Remove a volume from a pgroup.

        :param volume: Name of the volume to remove from pgroup.
        :type volume: str
        :param pgroup: pgroup from which to remove volume.
        :type pgroup: str

        :returns: A dictionary mapping "name" to volume and "protection_group"
                  to pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.7 or later.

        """
        return self._request("DELETE", "volume/{0}/pgroup/{1}".format(volume, pgroup))

    def list_volume_block_differences(self, volume, **kwargs):
        """Return a list of block differences for the specified volume.

        :param volume: Name of the volume to get information about.
        :type volume: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET volume/:volume/diff**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing block differences between
                  the specified volume and the base volume.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.3 or later.

        """
        return self._request("GET", "volume/{0}/diff".format(volume), kwargs)

    def list_volume_private_connections(self, volume, **kwargs):
        """Return a list of dictionaries describing connected hosts.

        :param volume: Name of the volume for which to list the private connections.
        :type volume: str

        :returns: A list of dictionaries describing the volume's private connections.
        :rtype: ResponseList

        """
        return self._request("GET", "volume/{0}/host".format(volume), kwargs)

    def list_volume_shared_connections(self, volume, **kwargs):
        """Return a list of dictionaries describing connected host groups.

        :param volume: Name of the volume for which to list the shared connections.
        :type volume: str

        :returns: A list of dictionaries describing the volume's shared connections.
        :rtype: ResponseList

        """
        return self._request("GET", "volume/{0}/hgroup".format(volume), kwargs)

    def list_volumes(self, **kwargs):
        """Return a list of dictionaries describing each volume.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET volume**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing each volume.
        :rtype: ResponseList

        """
        return self._request("GET", "volume", kwargs)

    def rename_volume(self, volume, name):
        """Rename a volume.

        :param volume: Name of the volume to be renamed.
        :type volume: str
        :param name: New name of volume to be renamed.
        :type name: str

        :returns: A dictionary mapping "name" to name.
        :rtype: ResponseDict

        .. note::

            All snapshots of the named volume also are renamed to the new name,
            followed by its previous suffix.

        """
        return self.set_volume(volume, name=name)

    def recover_volume(self, volume):
        """Recover a volume that has been destroyed but not eradicated.

        :param volume: Name of volume to be recovered.
        :type volume: str

        :returns: A dictionary mapping "name" to volume.
        :rtype: ResponseDict

        .. note::

            This must be done within the 24 hours following a volume's
            destruction or it will be eradicated.

        """
        return self.set_volume(volume, action="recover")

    def truncate_volume(self, volume, size):
        """Truncate a volume to a new, smaller size.

        :param volume: Name of the volume to truncate.
        :type volume: str
        :param size: Size in bytes, or string representing the size of the
                     volume to be created.
        :type size: int or str

        :returns: A dictionary mapping "name" to volume and "size" to the
                  volume's new size in bytes.
        :rtype: ResponseDict

        .. warnings also::

            Data may be irretrievably lost in this operation.

        .. note::

            A snapshot of the volume in its previous state is taken and
            immediately destroyed, but it is available for recovery for
            the 24 hours following the truncation.

        """
        return self.set_volume(volume, size=size, truncate=True)

    def move_volume(self, volume, container):
        """Move a volume to a new pod or vgroup

        :param volume: Name of the volume to move.
        :type volume: str
        :param container: Destination container of the move, either
                          a pod, a vgroup or "" for the local array.
        :type container: str

        :returns: a dictionary describing the volume, with new container
                  reflected in new name.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.13 or later.

        """
        return self.set_volume(volume, container=container)

    #
    # Host management methods
    #

    def connect_host(self, host, volume, **kwargs):
        """Create a connection between a host and a volume.

        :param host: Name of host to connect to volume.
        :type host: str
        :param volume: Name of volume to connect to host.
        :type volume: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST host/:host/volume/:volume**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the connection between the host and volume.
        :rtype: ResponseDict

        """
        return self._request(
            "POST", "host/{0}/volume/{1}".format(host, volume), kwargs)

    def create_host(self, host, **kwargs):
        """Create a host are return a dictionary describing it.

        :param host: Name of host to be created.
        :type host: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST host/:host**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the created host.
        :rtype: ResponseDict

        """
        return self._request("POST", "host/{0}".format(host), kwargs)

    def delete_host(self, host):
        """Delete a host.

        :param host: Name of host to be deleted.
        :type host: str

        :returns: A dictionary mapping "name" to host.
        :rtype: ResponseDict

        """
        return self._request("DELETE", "host/{0}".format(host))

    def disconnect_host(self, host, volume):
        """Delete a connection between a host and a volume.

        :param host: Name of host to be disconnected from volume.
        :type host: str
        :param volume: Name of volume to be disconnected from host.
        :type volume: str

        :returns: A dictionary mapping "name" to host and "vol" to volume.
        :rtype: ResponseDict

        """
        return self._request("DELETE", "host/{0}/volume/{1}".format(host,
                                                                    volume))

    def get_host(self, host, **kwargs):
        """Return a dictionary describing a host.

        :param host: Name of host to get information about.
        :type host: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET host/:host**
        :type \*\*kwargs: optional

        :returns: A dictionary describing host.
        :rtype: ResponseDict

        """
        return self._request("GET", "host/{0}".format(host), kwargs)

    def add_host(self, host, pgroup):
        """Add a host to a pgroup.

        :param host: Name of the host to add to pgroup.
        :type host: str
        :param pgroup: pgroup to which to add host.
        :type pgroup: str

        :returns: A dictionary mapping "name" to host and "protection_group"
                  to pgroup.
        :rtype: ResponseDict

        """
        return self._request("POST", "host/{0}/pgroup/{1}".format(host, pgroup))

    def remove_host(self, host, pgroup):
        """Remove a host from a pgroup.

        :param host: Name of the host to remove from pgroup.
        :type host: str
        :param pgroup: pgroup from which to remove host.
        :type pgroup: str

        :returns: A dictionary mapping "name" to host and "protection_group"
                  to pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.7 or later.

        """
        return self._request("DELETE", "host/{0}/pgroup/{1}".format(host, pgroup))

    def list_host_connections(self, host, **kwargs):
        """Return a list of dictionaries describing connected volumes.

        :type host: str
            Name of host for which to list connections.
        :type \*\*kwargs: optional
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET host/:host/volume**

        :returns: A list of dictionaries describing host's connections.
        :rtype: ResponseList

        """
        return self._request("GET", "host/{0}/volume".format(host), kwargs)

    def list_hosts(self, **kwargs):
        """Return a list of dictionaries describing each host.

        :type \*\*kwargs: optional
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET host**

        :returns: A list of dictionaries describing each host.
        :rtype: ResponseList

        """
        return self._request("GET", "host", kwargs)

    def rename_host(self, host, name):
        """Rename a host.

        :param host: Name of host to be renamed.
        :type host: str
        :param name: New name of host to be renamed.
        :type name: str

        :returns: A dictionary mapping "name" to name.
        :rtype: ResponseDict

        """
        return self.set_host(host, name=name)

    def set_host(self, host, **kwargs):
        """Set an attribute of a host.

        :param host: Name of host for which to set attribute.
        :type host: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT host/:host**
        :type \*\*kwargs: optional

        :returns: A dictionary describing host.
        :rtype: ResponseDict

        """
        return self._request("PUT", "host/{0}".format(host), kwargs)

    #
    # Host group management methods
    #

    def connect_hgroup(self, hgroup, volume, **kwargs):
        """Create a shared connection between a host group and a volume.

        :param hgroup: Name of hgroup to connect to volume.
        :type hgroup: str
        :param volume: Name of volume to connect to hgroup.
        :type volume: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST hgroup/:hgroup/volume/:volume**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the connection between the hgroup and volume.
        :rtype: ResponseDict

        """
        return self._request(
            "POST", "hgroup/{0}/volume/{1}".format(hgroup, volume), kwargs)

    def create_hgroup(self, hgroup, **kwargs):
        """Create a host group and return a dictionary describing it.

        :param hgroup: Name of hgroup to be created.
        :type hgroup: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST hgroup/:hgroup**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the created hgroup
        :rtype: ResponseDict

        """
        return self._request("POST", "hgroup/{0}".format(hgroup), kwargs)

    def delete_hgroup(self, hgroup):
        """Delete a host group.

        :param hgroup: Name of the hgroup to be deleted.
        :type hgroup: str

        :returns: A dictionary mapping "name" to hgroup.
        :rtype: ResponseDict

        """
        return self._request("DELETE", "hgroup/{0}".format(hgroup))

    def disconnect_hgroup(self, hgroup, volume):
        """Delete a shared connection between a host group and a volume.

        :param hgroup: Name of hgroup to be disconnected from volume.
        :type hgroup: str
        :param volume: Name of volume to be disconnected from hgroup.
        :type volume: str

        :returns: A dictionary mapping "name" to hgroup and "vol" to volume.
        :rtype: ResponseDict

        """
        return self._request("DELETE",
                             "hgroup/{0}/volume/{1}".format(hgroup, volume))

    def get_hgroup(self, hgroup, **kwargs):
        """Return a list of dictionaries describing a host group.

        :param hgroup: Name of hgroup to get information about.
        :type hgroup: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET hgroup/:hgroup**
        :type \*\*kwargs: optional

        :returns: A dictionary describing hgroup.
        :rtype: ResponseDict

        """
        return self._request("GET", "hgroup/{0}".format(hgroup), kwargs)

    def add_hgroup(self, hgroup, pgroup):
        """Add an hgroup to a pgroup.

        :param hgroup: Name of the hgroup to add to pgroup.
        :type hgroup: str
        :param pgroup: pgroup to which to add hgroup.
        :type pgroup: str

        :returns: A dictionary mapping "name" to hgroup and "protection_group"
                  to pgroup.
        :rtype: ResponseDict

        """
        return self._request("POST", "hgroup/{0}/pgroup/{1}".format(hgroup, pgroup))

    def remove_hgroup(self, hgroup, pgroup):
        """Remove an hgroup from a pgroup.

        :param hgroup: Name of the hgroup to remove from pgroup.
        :type hgroup: str
        :param pgroup: pgroup from which to remove hgroup.
        :type pgroup: str

        :returns: A dictionary mapping "name" to hgroup and "protection_group"
                  to pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.7 or later.

        """
        return self._request("DELETE", "hgroup/{0}/pgroup/{1}".format(hgroup, pgroup))

    def list_hgroup_connections(self, hgroup):
        """Return a list of dictionaries describing shared connected volumes.

        :param hgroup: Name of hgroup for which to list connections.
        :type hgroup: str

        :returns: A list of dictionaries describing hgroup's connections.
        :rtype: ResponseList

        """
        return self._request("GET", "hgroup/{0}/volume".format(hgroup))

    def list_hgroups(self, **kwargs):
        """Return a list of dictionaries describing each host group.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET hgroup**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing each hgroup.
        :rtype: ResponseList

        """
        return self._request("GET", "hgroup", kwargs)

    def rename_hgroup(self, hgroup, name):
        """Rename a host group.

        :param hgroup: Name of hgroup to be renamed.
        :type hgroup: str
        :param name: New name of hgroup to be renamed.
        :type name: str

        :returns: A dictionary mapping "name" to name.
        :rtype: ResponseDict

        """
        return self.set_hgroup(hgroup, name=name)

    def set_hgroup(self, hgroup, **kwargs):
        """Set an attribute of a host group.

        :param hgroup: Name of hgroup for which to set attribute.
        :type hgroup: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT hgroup/:hgroup**
        :type \*\*kwargs: optional

        :returns: A dictionary describing hgroup.
        :rtype: ResponseDict

        """
        return self._request("PUT", "hgroup/{0}".format(hgroup), kwargs)

    #
    # Offload management methods
    #

    def connect_nfs_offload(self, name, **kwargs):
        """Connect an offload nfs target.

        :param name: Name of offload nfs target to be connected.
        :type name: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST offload/nfs/{}**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the nfs target.
        :rtype: ResponseDict

        """
        return self._request("POST", "nfs_offload/{0}".format(name), kwargs)

    def connect_s3_offload(self, name, **kwargs):
        """Connect an offload S3 target.

        :param name: Name of offload S3 target to be connected.
        :type name: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST offload/s3/{}**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the S3 target.
        :rtype: ResponseDict

        """
        return self._request("POST", "s3_offload/{0}".format(name), kwargs)

    def disconnect_nfs_offload(self, name):
        """Disconnect an nfs offload target.

        :param name: Name of nfs offload target to be disconnected.
        :type name: str

        :returns: A dictionary describing the target.
        :rtype: ResponseDict

        """
        return self._request("DELETE", "nfs_offload/{0}".format(name))

    def disconnect_s3_offload(self, name):
        """Disconnect an S3 offload target.

        :param name: Name of S3 offload target to be disconnected.
        :type name: str

        :returns: A dictionary describing the target.
        :rtype: ResponseDict

        """
        return self._request("DELETE", "s3_offload/{0}".format(name))

    def list_offload(self, **kwargs):
        """Return a list of dictionaries describing connected offload targets.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET offload**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing offload connections.
        :rtype: ResponseList

        """
        return self._request("GET", "offload", kwargs)

    def list_nfs_offload(self, **kwargs):
        """Return a list of dictionaries describing connected nfs offload targets.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET offload/nfs**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing NFS offload connections.
        :rtype: ResponseList

        """
        return self._request("GET", "nfs_offload", kwargs)

    def list_s3_offload(self, **kwargs):
        """Return a list of dictionaries describing connected S3 offload targets.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET offload/s3**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing S3 offload connections.
        :rtype: ResponseList

        """
        return self._request("GET", "s3_offload", kwargs)

    def get_offload(self, name, **kwargs):
        """Return a dictionary describing the connected offload target.

        :param offload: Name of offload target to get information about.
        :type offload: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET offload/::offload**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the offload connection.
        :rtype: ResponseDict

        """
        # Unbox if a list to accommodate a bug in REST 1.14
        result = self._request("GET", "offload/{0}".format(name), kwargs)
        if isinstance(result, list):
            headers = result.headers
            result = ResponseDict(result[0])
            result.headers = headers
        return result

    def get_nfs_offload(self, name, **kwargs):
        """Return a dictionary describing the connected nfs offload target.

        :param offload: Name of NFS offload target to get information about.
        :type offload: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET offload/nfs/::offload**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the nfs offload connection.
        :rtype: ResponseDict

        """
        # Unbox if a list to accommodate a bug in REST 1.14
        result = self._request("GET", "nfs_offload/{0}".format(name), kwargs)
        if isinstance(result, list):
            headers = result.headers
            result = ResponseDict(result[0])
            result.headers = headers
        return result

    def get_s3_offload(self, name, **kwargs):
        """Return a dictionary describing the connected S3 offload target.

        :param offload: Name of S3 offload target to get information about.
        :type offload: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET offload/s3/::offload**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the S3 offload connection.
        :rtype: ResponseDict

        """
        return self._request("GET", "s3_offload/{0}".format(name), kwargs)

    #
    # Network management methods
    #

    def disable_network_interface(self, interface):
        """Disable a network interface.

        :param interface: Name of network interface to be disabled.
        :type interface: str

        :returns: A dictionary describing the interface.
        :rtype: ResponseDict

        """
        return self.set_network_interface(interface, enabled=False)

    def enable_network_interface(self, interface):
        """Enable a network interface.

        :param interface: Name of network interface to be enabled.
        :type interface: str

        :returns: A dictionary describing the interface.
        :rtype: ResponseDict

        """
        return self.set_network_interface(interface, enabled=True)

    def get_network_interface(self, interface):
        """Return a dictionary describing a network interface.

        :param interface: Name of network interface to get information about.
        :type interface: str

        :returns: A dictionary describing the interface.
        :rtype: ResponseDict

        """
        return self._request("GET", "network/{0}".format(interface))

    def list_network_interfaces(self):
        """Get a list of dictionaries describing network interfaces.

        :returns: A list of dictionaries describing each network interface.
        :rtype: ResponseList

        """
        return self._request("GET", "network")

    def set_network_interface(self, interface, **kwargs):
        """Set network interface attributes.

        :param interface: Name of network interface for which to set attribute.
        :type interface: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT network/:network_component**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the interface.
        :rtype: ResponseDict

        """
        return self._request("PUT", "network/{0}".format(interface), kwargs)

    def create_subnet(self, subnet, prefix, **kwargs):
        """Create a subnet.

        :param subnet: Name of subnet to be created.
        :type subnet: str
        :param prefix: Routing prefix of subnet to be created.
        :type prefix: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST subnet/:subnet**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the created subnet.
        :rtype: ResponseDict

        .. note::

            prefix should be specified as an IPv4 CIDR address.
            ("xxx.xxx.xxx.xxx/nn", representing prefix and prefix length)

        .. note::

            Requires use of REST API 1.5 or later.

        """
        data = {"prefix": prefix}
        data.update(kwargs)
        return self._request("POST", "subnet/{0}".format(subnet), data)

    def delete_subnet(self, subnet):
        """Delete a subnet.

        :param subnet: Name of the subnet to be deleted.
        :type subnet: str

        :returns: A dictionary mapping "name" to subnet.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.5 or later.

        """
        return self._request("DELETE", "subnet/{0}".format(subnet))

    def disable_subnet(self, subnet):
        """Disable a subnet.

        :param subnet: Name of subnet to be disabled.
        :type subnet: str

        :returns: A dictionary describing the subnet.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.5 or later.

        """
        return self.set_subnet(subnet, enabled=False)

    def enable_subnet(self, subnet):
        """Enable a subnet.

        :param subnet: Name of subnet to be enabled.
        :type subnet: str

        :returns: A dictionary describing the subnet.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.5 or later.

        """
        return self.set_subnet(subnet, enabled=True)

    def get_subnet(self, subnet):
        """Return a dictionary describing a subnet.

        :param subnet: Name of the subnet to get information about.
        :type subnet: str

        :returns: A dictionary describing the subnet.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.5 or later.

        """
        return self._request("GET", "subnet/{0}".format(subnet))

    def list_subnets(self, **kwargs):
        """Get a list of dictionaries describing subnets.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET subnet**

        :type \*\*kwargs: optional
        :returns: A list of dictionaries describing each subnet.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.5 or later.

        """
        return self._request("GET", "subnet", kwargs)

    def rename_subnet(self, subnet, name):
        """Rename a subnet.

        :param subnet: Current name of the subnet to be renamed.
        :type subnet: str
        :param name: New name of the subnet to be renamed.
        :type name: str

        :returns: A dictionary describing the renamed subnet.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.5 or later.

        """
        return self.set_subnet(subnet, name=name)

    def set_subnet(self, subnet, **kwargs):
        """Set subnet attributes.

        :param subnet: Name of subnet for which to set attribute.
        :type subnet: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT subnet/:subnet**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the subnet.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.5 or later.

        """
        return self._request("PUT", "subnet/{0}".format(subnet), kwargs)

    def create_vlan_interface(self, interface, subnet, **kwargs):
        """Create a vlan interface

        :param interface: Name of interface to be created.
        :type interface: str
        :param subnet: Subnet associated with interface to be created
        :type subnet: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST network/vif/:vlan_interface**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the created interface
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.5 or later.

        """
        data = {"subnet": subnet}
        data.update(kwargs)
        return self._request("POST", "network/vif/{0}".format(interface), data)

    def delete_vlan_interface(self, interface):
        """Delete a vlan interface.

        :param interface: Name of the interface to be deleted.
        :type interface: str

        :returns: A dictionary mapping "name" to interface.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.5 or later.

        """
        return self._request("DELETE", "network/{0}".format(interface))

    # DNS methods

    def get_dns(self):
        """Get current DNS settings.

        :returns: A dictionary describing current DNS settings.
        :rtype: ResponseDict

        """
        return self._request("GET", "dns")

    def set_dns(self, **kwargs):
        """Set DNS settings.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT dns**
        :type \*\*kwargs: optional

        :returns: A dictionary describing current DNS settings.
        :rtype: ResponseDict

        """
        return self._request("PUT", "dns", kwargs)

    # ports

    def list_ports(self, **kwargs):
        """List SAN ports.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET port**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing each port.
        :rtype: ResponseList

        """
        return self._request("GET", "port", kwargs)

    #
    # Hardware management methods
    #

    def get_drive(self, drive):
        """Get drive attributes.

        :param drive: Name of drive to get information about.
        :type drive: str

        :returns: A dictionary describing drive.
        :rtype: ResponseDict

        """
        return self._request("GET", "drive/{0}".format(drive))

    def list_drives(self):
        """Returns a list of dictionaries describing SSD and NVRAM modules.

        :returns: A list of dictionaries describing each drive.
        :rtype: ResponseList

        """
        return self._request("GET", "drive")

    def get_hardware(self, component, **kwargs):
        """Returns a dictionary describing a hardware component.

        :param component: Name of hardware component to get information about.
        :type component: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET hardware/:component**
        :type \*\*kwargs: optional

        :returns: A dictionary describing component.
        :rtype: ResponseDict

        """
        return self._request("GET", "hardware/{0}".format(component), kwargs)

    def list_hardware(self, **kwargs):
        """Returns a list of dictionaries describing hardware.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET hardware**

        :type \*\*kwargs: optional
        :returns: A list of dictionaries describing each hardware component.
        :rtype: ResponseList

        """
        return self._request("GET", "hardware", kwargs)

    def set_hardware(self, component, **kwargs):
        """Set an attribute of a hardware component.

        :param component: Name of component for which to set attribute.
        :type component: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT hardware/:component**
        :type \*\*kwargs: optional

        :returns: A dictionary describing component.
        :rtype: ResponseDict

        """
        return self._request("PUT", "hardware/{0}".format(component), kwargs)

    #
    # User-management methods
    #

    def _list_admin(self, **kwargs):
        """Return a list of dictionaries describing remote access.

        For the arguments you can provide to this method, see the REST API Guide
        on your array for the documentation on the request:

        GET admin.
        """
        return self._request("GET", "admin", kwargs)

    def list_admins(self, **kwargs):
        """Return a list of dictionaries describing local admins.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET admin**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries mapping "name" to a username and
                  "role" to their role for each local admin on the array.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.14 or later.
        """
        return self._list_admin(**kwargs)

    def create_admin(self, admin, **kwargs):
        """Create an admin.

        :param admin: Name of admin.
        :type admin: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST admin/:admin**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the new admin.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.14 or later.

        """
        return self._request("POST", "admin/{0}".format(admin), kwargs)

    def delete_admin(self, admin):
        """Delete an admin.

        :param admin: Name of admin whose API token is to be deleted.
        :type admin: str

        :returns: A dictionary mapping "name" to admin and "api_token" to None.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.14 or later.
        """
        return self._request("DELETE", "admin/{0}".format(admin))

    def set_admin(self, admin, **kwargs):
        """Set an attribute of an admin.

        :param admin: Name of admin for whom to set an attribute.
        :type admin: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT admin/:admin**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the admin.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.14 or later.
        """
        return self._request("PUT", "admin/{0}".format(admin), kwargs)

    def create_api_token(self, admin, **kwargs):
        """Create an API token for an admin.

        :param admin: Name of admin for whom to create an API token.
        :type admin: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST admin/:admin/apitoken**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the new API token.
        :rtype: ResponseDict

        """
        return self._request("POST", "admin/{0}/apitoken".format(admin), kwargs)

    def delete_api_token(self, admin):
        """Delete the API token of an admin.

        :param admin: Name of admin whose API token is to be deleted.
        :type admin: str

        :returns: A dictionary mapping "name" to admin and "api_token" to None.
        :rtype: ResponseDict

        """
        return self._request("DELETE", "admin/{0}/apitoken".format(admin))

    def get_admin(self, admin):
        """Returns a dictionary describing an admin.

        :param admin: Name of admin to get.
        :type admin: str

        :returns: A dictionary mapping "name" to admin and "role" to their role.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.14 or later.

        """
        return self._request("GET", "admin/{0}".format(admin))

    def get_publickey(self, admin):
        """Returns a dictionary describing an admin's public key.

        :param admin: Name of admin whose public key to get.
        :type admin: str

        :returns: A dictionary mapping "name" to admin and "publickey" to "\*\*\*\*".
        :rtype: ResponseDict

        """
        return self._request("GET", "admin/{0}".format(admin),
                             {"publickey": True})

    def get_api_token(self, admin, **kwargs):
        """Return a dictionary describing an admin's API token.

        :param admin: Name of admin whose API token to get.
        :type admin: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET admin/:admin/apitoken**
        :type \*\*kwargs: optional

        :returns: A dictionary describing admin's API token.
        :rtype: ResponseDict

        """
        return self._request("GET", "admin/{0}/apitoken".format(admin))

    def list_publickeys(self):
        """Return a list of dictionaries describing public keys.

        :returns: A list of dictionaries mapping "name" to a username and
                  "publickey" to "\*\*\*\*" for each admin with a public
                  key set.
        :rtype: ResponseList

        """
        return self._list_admin(publickey=True)

    def list_api_tokens(self, **kwargs):
        """Return a list of dictionaries describing REST API tokens.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET admin**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing the API token of each admin
                  with an API token set.
        :rtype: ResponseList

        .. note::

            The API tokens are replaced with "\*\*\*\*" unless the parameter
            expose is passed as True.

        """
        return self._list_admin(api_token=True, **kwargs)

    def refresh_admin(self, admin, **kwargs):
        """Refresh the admin permission cache for the specified admin.

        :param admin: Name of admin whose permission cache is to be refreshed.
        :type admin: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET admin**
        :type \*\*kwargs: optional

        :returns: A dictionary mapping "name" to admin and "role" to the admin's role.
        :rtype: ResponseDict

        .. note::

            Setting the optional parameter clear to True only clears the
            cache without doing an LDAP lookup to get new permissions.

        """
        return self.set_admin(admin, action="refresh", **kwargs)

    def refresh_admins(self):
        """Clear the admin permission cache.

        :returns: A dictionary mapping "name" to "[ALL]" and "role" to None.
        :rtype: ResponseDict

        .. note::

            Does not immediately do any LDAP lookups to get new permissions.

        """
        return self._request("PUT", "admin",
                             {"action": "refresh", "clear": True})

    def set_publickey(self, admin, key):
        """Set the public key associated with an admin.

        :param admin: Name of admin whose public key is to be set.
        :type admin: str
        :param key: New public key for admin.
        :type key: str

        :returns: A dictionary mapping "name" to admin and "publickey"
                  to "\*\*\*\*"
        :rtype: ResponseDict

        """
        return self.set_admin(admin, publickey=key)

    def set_password(self, admin, new_password, old_password):
        """Set an admin's password.

        :param admin: Name of admin whose password is to be set.
        :type admin: str
        :param new_password: New password for admin.
        :type new_password: str
        :param old_password: Current password of admin.
        :type old_password: str

        :returns: A dictionary mapping "name" to admin.
        :rtype: ResponseDict

        """
        return self.set_admin(admin, password=new_password,
                              old_password=old_password)

    # Directory Service methods

    def disable_directory_service(self, check_peer=False):
        """Disable the directory service.

        :param check_peer: If True, disables server authenticity
                           enforcement. If False, disables directory
                           service integration.
        :type check_peer: bool, optional

        :returns: A dictionary describing the status of the directory service.
        :rtype: ResponseDict

        """
        if check_peer:
            return self.set_directory_service(check_peer=False)
        return self.set_directory_service(enabled=False)

    def enable_directory_service(self, check_peer=False):
        """Enable the directory service.

        :param check_peer: If True, enables server authenticity
                           enforcement. If False, enables directory
                           service integration.
        :type check_peer: bool, optional

        :returns: A dictionary describing the status of the directory service.
        :rtype: ResponseDict

        """
        if check_peer:
            return self.set_directory_service(check_peer=True)
        return self.set_directory_service(enabled=True)

    def get_directory_service(self, **kwargs):
        """Return a dictionary describing directory service configuration.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET directoryservice**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the status of the directory service.
        :rtype: ResponseDict

        """
        return self._request("GET", "directoryservice", kwargs)

    def set_directory_service(self, **kwargs):
        """Set an attribute of the directory service configuration.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT directoryservice**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the status of the directory service.
        :rtype: ResponseDict

        """
        return self._request("PUT", "directoryservice", kwargs)

    def test_directory_service(self):
        """Test the directory service.

        :returns: A dictionary mapping "output" to the output of the directory
                  service test.
        :rtype: ResponseDict

        """
        return self.set_directory_service(action="test")

    def list_directory_service_roles(self, **kwargs):
        """Get directory service groups for roles.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET directoryservice/role**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing the group
                  and group base for each role.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.16 or later.

        """
        return self._request("GET", "directoryservice/role", kwargs)

    def set_directory_service_roles(self, **kwargs):
        """Set directory service groups for roles.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT directoryservice/role**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing the group
                  and group base for each role changed.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.16 or later.

        """
        return self._request("PUT", "directoryservice/role", kwargs)

    #
    # Global admin methods
    #

    def get_global_admin_attributes(self):
        """Return a dictionary describing the existing global admin attributes.

        :returns: A dictionary describing the existing global admin attributes.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.16 or later.

        """
        return self._request("GET", "admin/settings")

    def set_global_admin_attributes(self, **kwargs):
        """Set the global admin attributes.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT admin/settings**

        :returns: A dictionary describing the global admin attributes.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.16 or later.

        """
        return self._request("PUT", "admin/settings", kwargs)

    def list_locked_admins_lockout_info(self):
        """Return a list of dictionaries describing lockout information for locked admins.

        :returns: A list of dictionaries describing all the locked admins
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.16 or later.

        """
        return self._list_admin(lockout=True)

    def get_admin_lockout_info(self, admin):
        """Return a dictionary describing lockout information for a specific admin.

        :param admin: Name of admin whose lockout info is requested
        :type admin: str

        :returns: A dictionary describing a specific locked admin
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.16 or later.

        """
        return self._request("GET", "admin/{0}".format(admin), {"lockout": True})

    def unlock_admin(self, admin):
        """Unlocks an admin

        :param admin: Name of admin to unlock
        :type admin: str

        :returns: A dictionary describing the newly unlocked admin
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.16 or later.

        """
        return self._request("DELETE", "admin/{0}/lockout".format(admin))

    #
    # Support related methods
    #

    def _set_phonehome(self, **kwargs):
        return self._request("PUT", "array/phonehome", kwargs)

    def _set_remote_assist(self, **kwargs):
        return self._request("PUT", "array/remoteassist", kwargs)

    def disable_phonehome(self):
        """Disable hourly phonehome.

        :returns: A dictionary mapping "phonehome" to "disabled".
        :rtype: ResponseDict

        """
        return self._set_phonehome(enabled=False)

    def disable_remote_assist(self):
        """Disable remote assist.

        :returns: A dictionary describing the status of the remote assist
                  connection.
        :rtype: ResponseDict

        """
        return self._set_remote_assist(action="disconnect")

    def enable_phonehome(self):
        """Enable hourly phonehome.

        :returns: A dictionary mapping "phonehome" to "enabled".
        :rtype: ResponseDict

        """
        return self._set_phonehome(enabled=True)

    def enable_remote_assist(self):
        """Enable remote assist.

        :returns: A dictionary describing the status of the remote assist
                  connection.
        :rtype: ResponseDict

        """
        return self._set_remote_assist(action="connect")

    def get_manual_phonehome_status(self):
        """Get manually-initiated phonehome status.

        :returns: A dictionary describing the current status of a
                  manually-initiated phonehome.
        :rtype: ResponseDict

        """
        return self._request("GET", "array/phonehome")

    def get_phonehome(self):
        """Return a dictionary describing if hourly phonehome is enabled.

        :returns: A dictionary mapping "phonehome" to "enabled" if hourly
                  phonehome is enabled, mapping to "disabled" otherwise.
        :rtype: ResponseDict

        """
        return self.get(phonehome=True)

    def get_remote_assist_status(self):
        """Return a dictionary describing whether remote assist is enabled.

        :returns: A dictionary describing the current status of the remote
                  assist connection.
        :rtype: ResponseDict

        """
        return self._request("GET", "array/remoteassist")

    def phonehome(self, action):
        """Manually initiate or cancel a phonehome action.

        :type action: str
        :param action: The timeframe of logs to phonehome or cancel the current
                       phonehome.

        .. note::

            action must be one of: ("send_today", "send_yesterday", "send_all", "cancel").

        :returns: A dictionary describing the current status of the phonehome request.
        :rtype: ResponseDict

        """
        return self._set_phonehome(action=action)

    #
    # Alerts and audit records
    #

    def _set_alert_recipient(self, address, **kwargs):
        return self._request("PUT", "alert/{0}".format(address), kwargs)

    def _set_message(self, message_id, **kwargs):
        return self._request("PUT", "message/{0}".format(message_id), kwargs)

    def clear_message(self, message_id):
        """Clear an alert message or audit record flag.

        :param message_id: ID of the message to unflag.
        :type message_id: int or str

        :returns: A dictionary mapping "id" to message_id.
        :rtype: ResponseDict

        """
        return self._set_message(message_id, flagged=False)

    def create_alert_recipient(self, address):
        """Add an alert recipient.

        :param address: Email address of alert recipient to be created.
        :type address: str

        :returns: A dictionary mapping "name" to address and "enabled" to True.
        :rtype: ResponseDict

        """
        return self._request("POST", "alert/{0}".format(address))

    def delete_alert_recipient(self, address):
        """Delete an alert recipient.

        :param address: Email address of alert recipient to be deleted.
        :type address: str

        :returns: A dictionary mapping "name" to address.
        :rtype: ResponseDict

        """
        return self._request("DELETE", "alert/{0}".format(address))

    def disable_alert_recipient(self, address):
        """Disable alerts to an alert recipient.

        :param address: Email address of alert recipient to be disabled.
        :type address: str

        :returns: A dictionary mapping "name" to address and "enabled" to False.
        :rtype: ResponseDict

        """
        return self._set_alert_recipient(address, enabled=False)

    def enable_alert_recipient(self, address):
        """Enable alerts to an alert recipient.

        :param address: Email address of alert recipient to be enabled.
        :type address: str

        :returns: A dictionary mapping "name" to address and "enabled" to True.
        :rtype: ResponseDict

        """
        return self._set_alert_recipient(address, enabled=True)

    def flag_message(self, message_id):
        """Flag an alert message or audit record.

        :param message_id: ID of message to be flagged.
        :type message_id: int or str

        :returns: A dictionary mapping "id" to message_id.
        :rtype: ResponseDict

        """
        return self._set_message(message_id, flagged=True)

    def get_alert_recipient(self, address):
        """Return a dictionary describing an alert recipient.

        :param address: Email address of alert recipient to get information about.
        :type address: str

        :returns: A dictionary mapping "name" to address and "enabled" to True
                  if that alert recipient is enabled, False otherwise.
        :rtype: ResponseDict

        """
        return self._request("GET", "alert/{0}".format(address))

    def list_alert_recipients(self):
        """Return a list of dictionaries describing alert recipients.

        :returns: A list of dictionaries mapping "name" to a recipient's
                  address and "enabled" to True if that recipient is enabled,
                  False otherwise, for each alert recipient.
        :rtype: ResponseList

        """
        return self._request("GET", "alert")

    def list_messages(self, **kwargs):
        """Return a list of alert messages.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET message**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing each message.
        :rtype: ResponseList

        """
        return self._request("GET", "message", kwargs)

    def test_alert(self):
        """Send test alerts to all recipients.

        :returns: A list of dictionaries describing the test outcome for each
                  recipient.
        :rtype: ResponseList

        """
        return self._request("PUT", "alert", {"action": "test"})

    def test_alert_recipient(self, address):
        """Send a test alert to the specified recipient.

        :param address: Address of recipient of test alert.
        :type address: str

        :returns: A dictionary describing the test outcome.
        :rtype: ResponseDict

        """
        return self._set_alert_recipient(address, action="test")

    #
    # SNMP managers
    #

    def create_snmp_manager(self, manager, host, **kwargs):
        """Create an SNMP manager.

        :param manager: Name of manager to be created.
        :type manager: str
        :param host: IP address or DNS name of SNMP server to be used.
        :type host: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST snmp/:manager**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the created SNMP manager.
        :rtype: ResponseDict

        """
        data = {"host": host}
        data.update(kwargs)
        return self._request("POST", "snmp/{0}".format(manager), data)

    def delete_snmp_manager(self, manager):
        """Delete an SNMP manager.

        :param manager: Name of SNMP manager to be deleted.
        :type manager: str

        :returns: A dictionary mapping "name" to manager.
        :rtype: ResponseDict

        """
        return self._request("DELETE", "snmp/{0}".format(manager))

    def get_snmp_engine_id(self):
        """Return the SNMP v3 engine ID generated for the array.

        :returns: A dictionary mapping "engine_id" to the array's SNMP engine ID.
        :rtype: ResponseDict

        .. note::

            Requires use of SNMP v3.

        """
        return self._request("GET", "snmp", {"engine_id": True})

    def get_snmp_manager(self, manager):
        """Return a dictionary describing an SNMP manager.

        :param manager: Name of SNMP manager to get information about.
        :type manager: str

        :returns: A dictionary describing manager.
        :rtype: ResponseDict

        """
        return self._request("GET", "snmp/{0}".format(manager))

    def list_snmp_managers(self):
        """Return a list of dictionaries describing SNMP managers.

        :returns: A list of dictionaries describing each SNMP manager.
        :rtype: ResponseList

        """
        return self._request("GET", "snmp")

    def rename_snmp_manager(self, manager, name):
        """Rename an SNMP manager.

        :param manager: Current name of the SNMP manager to be renamed.
        :type manager: str
        :param name: New name of the SNMP manager to be renamed.
        :type name: str

        :returns: A dictionary describing the renamed SNMP manager.
        :rtype: ResponseDict

        """
        return self.set_snmp_manager(manager, name=name)

    def set_snmp_manager(self, manager, **kwargs):
        """Set an attribute of an SNMP manager.

        :param manager: Name of the SNMP manager for which to set an attribute.
        :type manager: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT snmp/:manager**
        :type \*\*kwargs: optional

        :returns: A dictionary describing manager.
        :rtype: ResponseDict

        """
        return self._request("PUT", "snmp/{0}".format(manager), kwargs)

    def test_snmp_manager(self, manager):
        """Send a test trap to a manager.

        :param manager: SNMP manager to which to send a test trap.
        :type manager: str

        :returns: A dictionary mapping "output" to the output of the test.
        :rtype: ResponseDict

        """
        return self.set_snmp_manager(manager, action="test")

    #
    # Replication related methods
    # Note: These methods only work with REST API 1.2 and later
    #

    def connect_array(self, address, connection_key, connection_type, **kwargs):
        """Connect this array with another one.

        :param address: IP address or DNS name of other array.
        :type address: str
        :param connection_key: Connection key of other array.
        :type connection_key: str
        :param connection_type: Type(s) of connection desired.
        :type connection_type: list
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST array/connection**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the connection to the other array.
        :rtype: ResponseDict

        .. note::

            Currently, the only type of connection is "replication".

        .. note::

            Requires use of REST API 1.2 or later.

        """
        data = {"management_address": address,
                "connection_key": connection_key,
                "type": connection_type}
        data.update(kwargs)
        return self._request("POST", "array/connection", data)

    def disconnect_array(self, address):
        """Disconnect this array from another one.

        :param address: IP address or DNS name of other array.
        :type address: str

        :returns: A dictionary mapping "name" to address.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self._request("DELETE",
                             "array/connection/{0}".format(address))

    def list_array_connections(self, **kwargs):
        """Return list of connected arrays.

        :returns: A list of dictionaries describing each connection to another array.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self._request("GET", "array/connection", kwargs)

    def throttle_array_connection(self, address, **kwargs):
        """Set bandwidth limits on a connection.

        :param address: IP address or DNS name of other array.
        :type address: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT array/connection/:address**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the connection to the other array.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.5 or later.

        """
        return self._request("PUT", "array/connection/{0}".format(address), kwargs)

    # Protection group related methods

    def create_pgroup(self, pgroup, **kwargs):
        """Create pgroup with specified name.

        :param pgroup: Name of pgroup to be created.
        :type pgroup: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST pgroup/:pgroup**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the created pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self._request("POST", "pgroup/{0}".format(pgroup), kwargs)

    def create_pgroup_snapshot(self, source, **kwargs):
        """Create snapshot of pgroup from specified source.

        :param source: Name of pgroup of which to take snapshot.
        :type source: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST pgroup**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the created snapshot.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        # In REST 1.4, support was added for snapshotting multiple pgroups. As a
        # result, the endpoint response changed from an object to an array of
        # objects. To keep the  response type consistent between REST versions,
        # we unbox the response when creating a single snapshot.
        result = self.create_pgroup_snapshots([source], **kwargs)
        if self._rest_version >= LooseVersion("1.4"):
            headers = result.headers
            result = ResponseDict(result[0])
            result.headers = headers
        return result

    def send_pgroup_snapshot(self, source, **kwargs):
        """ Send an existing pgroup snapshot to target(s)

        :param source: Name of pgroup snapshot to send.
        :type source: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST pgroup**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing the sent snapshots.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.16 or later.

        """
        data = {"name": [source], "action":"send"}
        data.update(kwargs)
        return self._request("POST", "pgroup", data)

    def create_pgroup_snapshots(self, sources, **kwargs):
        """Create snapshots of pgroups from specified sources.

        :param sources: Names of pgroups of which to take snapshots.
        :type sources: list of str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST pgroup**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing the created snapshots.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.2 or later.

        """
        data = {"source": sources, "snap": True}
        data.update(kwargs)
        return self._request("POST", "pgroup", data)

    def destroy_pgroup(self, pgroup, **kwargs):
        """Destroy an existing pgroup or pgroup snapshot.

        :param pgroup: Name of pgroup(snap) to be destroyed.
        :type pgroup: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST pgroup/:pgroup**
        :type \*\*kwargs: optional

        :returns: A dictionary mapping "name" to pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self._request("DELETE", "pgroup/{0}".format(pgroup), kwargs)

    def disable_pgroup_replication(self, pgroup):
        """Disable replication schedule for pgroup.

        :param pgroup: Name of pgroup for which to disable replication schedule.
        :type pgroup: str

        :returns: A dictionary describing pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self.set_pgroup(pgroup, replicate_enabled=False)

    def enable_pgroup_replication(self, pgroup):
        """Enable replication schedule for pgroup.

        :param pgroup: Name of pgroup for which to enable replication schedule.
        :type pgroup: str

        :returns: A dictionary describing pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self.set_pgroup(pgroup, replicate_enabled=True)

    def disable_pgroup_snapshots(self, pgroup):
        """Disable snapshot schedule for pgroup.

        :type pgroup: str
            Name of pgroup for which to disable snapshot schedule.

        :rtype: ResponseDict
        :returns: A dictionary describing pgroup.

        .. note::

            Requires use of REST API 1.2 or later.
        """
        return self.set_pgroup(pgroup, snap_enabled=False)

    def enable_pgroup_snapshots(self, pgroup):
        """Enable snapshot schedule for pgroup.

        :param pgroup: Name of pgroup for which to enable snapshot schedule.
        :type pgroup: str

        :returns: A dictionary describing pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self.set_pgroup(pgroup, snap_enabled=True)

    def eradicate_pgroup(self, pgroup, **kwargs):
        """Eradicate a destroyed pgroup.

        :param pgroup: Name of pgroup to be eradicated.
        :type pgroup: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **DELETE pgroup/:pgroup**
        :type \*\*kwargs: optional

        :returns: A dictionary mapping "name" to pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        eradicate = {"eradicate": True}
        eradicate.update(kwargs)
        return self._request("DELETE", "pgroup/{0}".format(pgroup), eradicate)

    def get_pgroup(self, pgroup, **kwargs):
        """Return dictionary describing a pgroup or snapshot.

        :param pgroup: Name of pgroup to get information about.
        :type pgroup: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET pgroup**
        :type \*\*kwargs: optional

        :returns: A list describing snapshots of the pgroup if the paramater
                  snap is passed as True, else a dictionary describing the
                  pgroup.
        :rtype: ResponseDict or ResponseList

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self._request("GET", "pgroup/{0}".format(pgroup), kwargs)

    def list_pgroups(self, **kwargs):
        """Return list dictionaries describing each pgroup.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET pgroup**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing each pgroup.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self._request("GET", "pgroup", kwargs)

    def recover_pgroup(self, pgroup, **kwargs):
        """Recover a destroyed pgroup that has not yet been eradicated.

        :param pgroup: Name of pgroup to be recovered.
        :type pgroup: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT pgroup/:pgroup**
        :type \*\*kwargs: optional

        :returns: A dictionary mapping "name" to pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self.set_pgroup(pgroup, action="recover", **kwargs)

    def rename_pgroup(self, pgroup, name):
        """Rename a pgroup.

        :param pgroup: Current name of pgroup to be renamed.
        :type pgroup: str
        :param name: New name of pgroup to be renamed.
        :type name: str

        :returns: A dictionary mapping "name" to name.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self.set_pgroup(pgroup, name=name)

    def set_pgroup(self, pgroup, **kwargs):
        """Set an attribute of a pgroup.

        :param pgroup: Name of pgroup for which to set attribute.
        :type pgroup: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT pgroup/:pgroup**
        :type \*\*kwargs: optional

        :returns: A dictionary describing pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self._request("PUT", "pgroup/{0}".format(pgroup), kwargs)

    def create_vgroup(self, vgroup, **kwargs):
        """Create a vgroup.

        :param vgroup: Name of vgroup to be created.
        :type vgroup: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST vgroup/:vgroup**
        :type \*\*kwargs: optional

        :returns: A dictionary mapping "name" to vgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.13 or later.

        """
        return self._request("POST", "vgroup/{0}".format(vgroup), kwargs)

    def destroy_vgroup(self, vgroup):
        """Destroy an existing vgroup.

        :param vgroup: Name of vgroup to be destroyed.
        :type vgroup: str

        :returns: A dictionary mapping "name" to vgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.13 or later.

        """
        return self._request("DELETE", "vgroup/{0}".format(vgroup))

    def eradicate_vgroup(self, vgroup):
        """Eradicate a destroyed vgroup.

        :param vgroup: Name of vgroup to be eradicated.
        :type vgroup: str

        :returns: A dictionary mapping "name" to vgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.13 or later.

        """
        return self._request("DELETE", "vgroup/{0}".format(vgroup),
                             {"eradicate": True})

    def get_vgroup(self, vgroup, **kwargs):
        """Return dictionary describing a vgroup.

        :param vgroup: Name of vgroup to get information about.
        :type vgroup: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET vgroup**
        :type \*\*kwargs: optional

        :returns: A list describing a dictionary describing the
                  vgroup.
        :rtype: ResponseDict or ResponseList

        .. note::

            Requires use of REST API 1.13 or later.

        """
        return self._request("GET", "vgroup/{0}".format(vgroup), kwargs)

    def list_vgroups(self, **kwargs):
        """Return list dictionaries describing each vgroup.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET vgroup**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing each vgroup.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.13 or later.

        """
        return self._request("GET", "vgroup", kwargs)

    def recover_vgroup(self, vgroup):
        """Recover a destroyed vgroup that has not yet been eradicated.

        :param vgroup: Name of vgroup to be recovered.
        :type vgroup: str

        :returns: A dictionary mapping "name" to vgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.13 or later.

        """
        return self.set_vgroup(vgroup, action="recover")

    def rename_vgroup(self, vgroup, name):
        """Rename a vgroup.

        :param vgroup: Current name of vgroup to be renamed.
        :type vgroup: str
        :param name: New name of vgroup to be renamed.
        :type name: str

        :returns: A dictionary mapping "name" to name.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.13 or later.

        """
        return self.set_vgroup(vgroup, name=name)

    def set_vgroup(self, vgroup, **kwargs):
        """Set an attribute of a vgroup.

        :param vgroup: Name of vgroup for which to set attribute.
        :type vgroup: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT vgroup/:vgroup**
        :type \*\*kwargs: optional

        :returns: A dictionary describing vgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.13 or later.

        """
        return self._request("PUT", "vgroup/{0}".format(vgroup), kwargs)
    #
    # Pod management methods
    # Note: These methods are not supported before REST API 1.13.
    #

    def set_pod(self, pod, **kwargs):
        """Perform actions on a pod and return a dictionary describing it.

        :param pod: Name of the for which to set attribute.
        :type pod: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                   documentation on the request:
                   **PUT pod/:pod**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the created pod.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.13 or later.

        """
        return self._request("PUT", "pod/{0}".format(pod), kwargs)

    def create_pod(self, pod, **kwargs):
        """Create a pod and return a dictionary describing it.

        :param pod: Name of the pod to be created.
        :type pod: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                   documentation on the request:
                   **POST pod**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the created pod.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.13 or later.
        """
        return self._request("POST", "pod/{0}".format(pod), kwargs)

    def clone_pod(self, source, dest, **kwargs):
        """Clone an existing pod to a new one.

        :param source: Name of the pod the be cloned.
        :type source: str
        :param dest: Name of the target pod to clone into
        :type dest: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **POST pod/:pod**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the created pod
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.13 or later.
        """
        data = {"source": source}
        data.update(kwargs)
        return self._request("POST", "pod/{0}".format(dest), data)

    def destroy_pod(self, pod):
        """Destroy an existing pod.

        :param pod: Name of the pod to be destroyed.
        :type pod: str

        :returns: A dictionary mapping "name" to pod, and the time remaining
                  before the pod is eradicated.
        :rtype: ResponseDict

        .. warnings also::

            This operation may cause a loss of data. The destroyed pod can
            be recovered during the 24 hours immediately following its
            destruction unless it is eradicated before then.

        .. note::

            Requires use of REST API 1.13 or later.
        """
        return self._request("DELETE", "pod/{0}".format(pod))

    def eradicate_pod(self, pod):
        """Eradicate a destroyed pod.

        :param pod: Name of the pod to be eradicated.
        :type pod: str

        :returns: A dictionary mapping "name" to pod.
        :rtype: ResponseDict

        .. note::

            This operation fails if pod is not destroyed.

        .. note::

            Requires use of REST API 1.13 or later.

        .. warnings also::

            This operation may permanently erase data and the pod cannot
            be recovered.

        """
        return self._request("DELETE", "pod/{0}".format(pod),
                             {"eradicate": True})

    def get_pod(self, pod, **kwargs):
        """Return a dictionary describing a pod.

        :param pod: Name of the pod to get information about.
        :type pod: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET pod/:pod**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the pod.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.13 or later.
        """
        return self._request("GET", "pod/{0}".format(pod), kwargs)

    def add_pod(self, pod, array):
        """Add arrays to a pod.

        :param pod: Name of the pod.
        :type pod: str
        :param array: Array to add to pod.
        :type array: str

        :returns: A dictionary mapping "name" to pod and "array" to the pod's
                  new array list.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.13 or later.
        """
        return self._request("POST", "pod/{0}/array/{1}".format(pod, array))

    def remove_pod(self, pod, array, **kwargs):
        """Remove arrays from a pod.

        :param pod: Name of the pod.
        :type pod: str
        :param array: Array to remove from pod.
        :type array: str
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **DELETE pod/:pod**/array/:array**
        :type \*\*kwargs: optional
        :returns: A dictionary mapping "name" to pod and "array" to the pod's
                  new array list.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.13 or later.
        """
        return self._request("DELETE", "pod/{0}/array/{1}".format(pod, array), kwargs)

    def list_pods(self, **kwargs):
        """Return a list of dictionaries describing each pod.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET pod**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing each pod.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.13 or later.
        """
        return self._request("GET", "pod", kwargs)

    def rename_pod(self, pod, name):
        """Rename a pod.

        :param pod: Name of the pod to be renamed.
        :type pod: str
        :param name: New name of pod to be renamed.
        :type name: str

        :returns: A dictionary mapping "name" to name.
        :rtype: ResponseDict

        .. note::

            All pod objects in the named pod also are renamed to the new name,
            followed by its previous suffix.

        .. note::

            Requires use of REST API 1.13 or later.
        """
        return self.set_pod(pod, name=name)

    def recover_pod(self, pod):
        """Recover a pod that has been destroyed but not eradicated.

        :param pod: Name of pod to be recovered.
        :type pod: str

        :returns: A dictionary mapping "name" to pod, and the time remaining
                  which will now be null.
        :rtype: ResponseDict

        .. note::

            This must be done within the 24 hours following a pod's
            destruction or it will be eradicated.

        .. note::

            Requires use of REST API 1.13 or later.
        """
        return self.set_pod(pod, action="recover")

    #
    # SSL Certificate related methods.
    # Note: These methods are not supported before REST API 1.3.
    #

    def get_certificate(self, **kwargs):
        """Get the attributes of the current array certificate.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET cert**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the configured array certificate.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.3 or later.

        """

        if self._rest_version >= LooseVersion("1.12"):
            return self._request("GET",
                "cert/{0}".format(kwargs.pop('name', 'management')), kwargs)
        else:
            return self._request("GET", "cert", kwargs)

    def list_certificates(self):
        """Get the attributes of the current array certificate.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET cert**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing all configured certificates.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.12 or later.

        """

        # This call takes no parameters.
        if self._rest_version >= LooseVersion("1.12"):
            return self._request("GET", "cert")
        else:
            # If someone tries to call this against a too-early api version,
            # do the best we can to provide expected behavior.
            cert = self._request("GET", "cert")
            out = ResponseList([cert])
            out.headers = cert.headers
            return out

    def get_certificate_signing_request(self, **kwargs):
        """Construct a certificate signing request (CSR) for signing by a
        certificate authority (CA).

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET cert/certificate_signing_request**
        :type \*\*kwargs: optional

        :returns: A dictionary mapping "certificate_signing_request" to the CSR.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.3 or later.

            In version 1.12, purecert was expanded to allow manipulation
            of multiple certificates, by name.  To preserve backwards compatibility,
            the default name, if none is specified, for this version is 'management'
            which acts on the certificate previously managed by this command.

        """
        if self._rest_version >= LooseVersion("1.12"):
            return self._request("GET",
                "cert/certificate_signing_request/{0}".format(
                    kwargs.pop('name', 'management')), kwargs)
        else:
            return self._request("GET", "cert/certificate_signing_request", kwargs)

    def set_certificate(self, **kwargs):
        """Modify an existing certificate, creating a new self signed one
        or importing a certificate signed by a certificate authority (CA).

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT cert**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the configured array certificate.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.3 or later.

            In version 1.12, purecert was expanded to allow manipulation
            of multiple certificates, by name.  To preserve backwards compatibility,
            the default name, if none is specified, for this version is 'management'
            which acts on the certificate previously managed by this command.

        """
        if self._rest_version >= LooseVersion("1.12"):
            return self._request("PUT",
                "cert/{0}".format(kwargs.pop('name', 'management')), kwargs)
        else:
            return self._request("PUT", "cert", kwargs)


    #
    # New SSL Certificate related methods.
    # Note: These methods are not supported before REST API 1.12.
    #

    def create_certificate(self, name, **kwargs):
        """Create a new managed certificate.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT cert**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the configured array certificate.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.12 or later.

            You may not create the management certificate, as it already exists.

        """
        return self._request("POST", "cert/{0}".format(name), kwargs)

    def delete_certificate(self, name, **kwargs):
        """Delete a managed certificate.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT cert**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the configured array certificate.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.12 or later.

            You may not delete the management certificate.

        """
        return self._request("DELETE", "cert/{0}".format(name), kwargs)

    #
    # New methods for KMIP configuration, introduced with version 1.12
    #

    def create_kmip(self, name, **kwargs):
        """Create a new kmip configuration.

        :param name: The name of the KMIP config to operate on.
        :type name: string
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT kmip**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing the kmip configuration.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.12 or later.

        """
        return self._request("POST", "kmip/{0}".format(name), kwargs)

    def delete_kmip(self, name):
        """Delete an existing kmip configuration.

        :param name: The name of the KMIP config to operate on.
        :type name: string

        :returns: A dictionary containing the name of the deleted kmip configuration.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.12 or later.

        """
        return self._request("DELETE", "kmip/{0}".format(name))

    def list_kmip(self):
        """Show all existing kmip configurations.

        :returns: A list of dictionaries containing the requested kmip configuration.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.12 or later.

        """
        return self._request("GET", "kmip")

    def get_kmip(self, name):
        """Show an existing kmip configuration.

        :param name: The name of the KMIP config to operate on.
        :type name: string

        :returns: A list of dictionaries containing the requested kmip configuration.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.12 or later.

        """
        return self._request("GET", "kmip/{0}".format(name))

    def set_kmip(self, name, **kwargs):
        """Modify an existing kmip configuration.

        :param name: The name of the KMIP config to operate on.
        :type name: string
        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT kmip**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing the modified kmip configuration.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.12 or later.

        """
        return self._request("PUT", "kmip/{0}".format(name), kwargs)

    def test_kmip(self, name):
        """Test a given kmip configuration.

        :param name: The name of the KMIP config to operate on.
        :type name: string

        :returns: A list of dictionaries containing per-server kmip test results.
        :rtype: ResponseList

        .. note::

            Requires use of REST API 1.12 or later.

        """
        return self._request("PUT", "kmip/{0}".format(name), {"action": "test"})

    #
    # Software management methods
    #

    def list_app_software(self, **kwargs):
        """List app software.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET software/app**
        :type \*\*kwargs: optional

        :param app: Name of app to get information about.
        :type app: str

        :returns: A dictionary describing app.
        :rtype: ResponseDict

        """
        return self._request("GET", "software/app", kwargs)

    def get_app_software(self, name, **kwargs):
        """List the specified app software.

        :param name: The name of the app.
        :type name: string

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET software/app/:app**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing the app.
        :rtype: ResponseList

        """
        return self._request("GET", "software/app/{0}".format(name), kwargs)

    def install_app(self, name, **kwargs):
        """Install the specified app.

        :param name: The name of the app.
        :type name: string

        :returns: A dictionary describing the app.
        :rtype: ResponseList

        """
        return self._request("POST", "software/app/{0}".format(name), kwargs)

    def uninstall_app(self, name, **kwargs):
        """Uninstall the specified app.

        :param name: The name of the app.
        :type name: string

        :returns: A dictionary describing the app.
        :rtype: ResponseList

        """
        return self._request("DELETE", "software/app/{0}".format(name), kwargs)

    #
    # App management methods
    #

    def list_apps(self, **kwargs):
        """Returns a list of dictionaries describing apps.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET app**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing each app.
        :rtype: ResponseList

        """
        return self._request("GET", "app", kwargs)

    def get_app(self, name, **kwargs):
        """Returns a list of dictionaries describing the app.

        :param name: The name of the app.
        :type name: string

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET app/:app**
        :type \*\*kwargs: optional

        :returns: A list of dictionaries describing the app.
        :rtype: ResponseList

        """
        return self._request("GET", "app/{0}".format(name), kwargs)

    def _set_app(self, name, **kwargs):
        return self._request("PUT", "app/{0}".format(name), kwargs)

    def enable_app(self, name):
        """Enable the specified app.

        :param name: Name of app to be enabled.
        :type name: str

        :returns: A dictionary describing the app.
        :rtype: ResponseList

        """
        return self._set_app(name, enabled=True)

    def disable_app(self, name):
        """Disable the specified app.

        :param name: Name of app to be disabled.
        :type name: str

        :returns: A dictionary describing the app.
        :rtype: ResponseList

        """
        return self._set_app(name, enabled=False)

    #
    # SMTP related methods.
    #

    def get_smtp(self):
        """Get the attributes of the current smtp server configuration.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **GET smtp**

        :returns: A dictionary describing the smtp server configuration.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.14 or later.
        """
        return self._request("GET", "smtp")

    def set_smtp(self, **kwargs):
        """Set the attributes of the current smtp server configuration.

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT smtp**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the smtp server configuration.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.14 or later.
        """
        return self._request("PUT", "smtp", kwargs)

    @staticmethod
    def page_through(page_size, function, *args, **kwargs):
        """Return an iterator over all pages of a REST operation.

        :param page_size: Number of elements to retrieve per call.
        :param function: FlashArray function that accepts limit as an argument.
        :param \*args: Positional arguments to be passed to function.
        :param \*\*kwargs: Keyword arguments to be passed to function.

        :returns: An iterator of tuples containing a page of results for the
                  function(\*args, \*\*kwargs) and None, or None and a PureError
                  if a call to retrieve a page fails.
        :rtype: iterator

        .. note::

            Requires use of REST API 1.7 or later.

            Only works with functions that accept limit as an argument.

            Iterator will retrieve page_size elements per call

            Iterator will yield None and an error if a call fails. The next
            call will repeat the same call, unless the caller sends in an
            alternate page token.

        """

        kwargs["limit"] = page_size

        def get_page(token):
            page_kwargs = kwargs.copy()
            if token:
                page_kwargs["token"] = token
            return function(*args, **page_kwargs)

        def page_generator():
            token = None
            while True:
                try:
                    response = get_page(token)
                    token = response.headers.get("x-next-token")
                except PureError as err:
                    yield None, err
                else:
                    if response:
                        sent_token = yield response, None
                        if sent_token is not None:
                            token = sent_token
                    else:
                        return

        return page_generator()


class ResponseList(list):
    """List type returned by FlashArray object.

    :ivar dict headers: The headers returned in the request.

    """
    def __init__(self, l=()):
        super(ResponseList, self).__init__(l)
        self.headers = {}

class ResponseDict(dict):
    """Dict type returned by FlashArray object.

    :ivar dict headers: The headers returned in the request.

    """
    def __init__(self, d=()):
        super(ResponseDict, self).__init__(d)
        self.headers = {}

class PureError(Exception):
    """Exception type raised by FlashArray object.

    :param reason: A message describing why the error occurred.
    :type reason: str

    :ivar str reason: A message describing why the error occurred.

    """
    def __init__(self, reason):
        self.reason = reason
        super(PureError, self).__init__()

    def __str__(self):
        return "PureError: {0}".format(self.reason)


class PureHTTPError(PureError):
    """Exception raised as a result of non-200 response status code.

    :param target: IP or DNS name of the array that received the HTTP request.
    :type target: str
    :param rest_version: The REST API version that was used when making the
                         request.
    :type rest_version: str
    :param response: The response of the HTTP request that caused the error.
    :type response: :class:`requests.Response`

    :ivar str target: IP or DNS name of the array that received the HTTP request.
    :ivar str rest_version: The REST API version that was used when making the
                            request.
    :ivar int code: The HTTP response status code of the request.
    :ivar dict headers: A dictionary containing the header information. Keys are
                        case-insensitive.
    :ivar str reason: The textual reason for the HTTP status code
                      (e.g. "BAD REQUEST").
    :ivar str text: The body of the response which may contain a message
                    explaining the error.

    .. note::

        The error message in text is not guaranteed to be consistent across REST
        versions, and thus should not be programmed against.

    """
    def __init__(self, target, rest_version, response):
        super(PureHTTPError, self).__init__(response.reason)
        self.target = target
        self.rest_version = rest_version
        self.code = response.status_code
        self.headers = response.headers
        self.text = response.text

    def __str__(self):
        msg = ("PureHTTPError status code {0} returned by REST "
               "version {1} at {2}: {3}\n{4}")
        return msg.format(self.code, self.rest_version, self.target,
                          self.reason, self.text)

