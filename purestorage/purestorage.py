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
VERSION = "1.11.2"


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

    """

    supported_rest_versions = [
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
                 user_agent=None):

        if not api_token and not (username and password):
            raise ValueError(
                "Must specify API token or both username and password.")
        elif api_token and (username or password):
            raise ValueError(
                "Specify only API token or both username and password.")

        self._cookies = {}
        self._target = target

        self._renegotiate_rest_version = False if rest_version else True

        self._verify_https = verify_https
        self._ssl_cert = ssl_cert

        self._user_agent = user_agent

        self._rest_version = rest_version
        if self._rest_version:
            self._rest_version = self._check_rest_version(rest_version)
        else:
            self._rest_version = self._choose_rest_version()

        self._api_token = (api_token or self._obtain_api_token(username, password))
        self._start_session()

    def _request(self, method, path, data=None, reestablish_session=True):
        """Perform HTTP request for REST API."""
        if path.startswith("https://"):
            url = path  # For cases where URL of different form is needed.
        else:
            url = "https://{0}/api/{1}/{2}".format(
                self._target, self._rest_version, path)
        headers = {"Content-Type": "application/json"}
        if self._user_agent:
            headers['User-Agent'] = self._user_agent

        body = json.dumps(data).encode("utf-8")
        verify = False
        if self._verify_https:
            if self._ssl_cert:
                verify = self._ssl_cert
            else:
                verify = True
        try:
            response = requests.request(method, url, data=body, headers=headers,
                                        cookies=self._cookies, verify=verify)
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

    def _set_volume(self, volume, **kwargs):
        """Perform actions on a volume and return a dictionary describing it."""
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

    def create_volume(self, volume, size):
        """Create a volume and return a dictionary describing it.

        :param volume: Name of the volume to be created.
        :type volume: str
        :param size: Size in bytes, or string representing the size of the
                     volume to be created.
        :type size: int or str

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
        return self._request("POST", "volume/{0}".format(volume), {"size":size})

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
        return self._set_volume(volume, size=size, truncate=False)

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
        return self._set_volume(volume, name=name)

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
        return self._set_volume(volume, action="recover")

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
        return self._set_volume(volume, size=size, truncate=True)

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

    def _set_admin(self, admin, **kwargs):
        """Set an attribute of an admin.

        For the arguments you can provide to this method, see the REST API Guide
        on your array for the documentation on the request:

        PUT admin/:user.
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
        return self._set_admin(admin, action="refresh", **kwargs)

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
        return self._set_admin(admin, publickey=key)

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
        return self._set_admin(admin, password=new_password,
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
        data = {"address": address,
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

    def destroy_pgroup(self, pgroup):
        """Destroy an existing pgroup.

        :param pgroup: Name of pgroup to be destroyed.
        :type pgroup: str

        :returns: A dictionary mapping "name" to pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self._request("DELETE", "pgroup/{0}".format(pgroup))

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

    def eradicate_pgroup(self, pgroup):
        """Eradicate a destroyed pgroup.

        :param pgroup: Name of pgroup to be eradicated.
        :type pgroup: str

        :returns: A dictionary mapping "name" to pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self._request("DELETE", "pgroup/{0}".format(pgroup),
                             {"eradicate": True})

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

    def recover_pgroup(self, pgroup):
        """Recover a destroyed pgroup that has not yet been eradicated.

        :param pgroup: Name of pgroup to be recovered.
        :type pgroup: str

        :returns: A dictionary mapping "name" to pgroup.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.2 or later.

        """
        return self.set_pgroup(pgroup, action="recover")

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

    #
    # SSL Certificate related methods.
    # Note: These methods only work with REST API 1.3 and later
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
        return self._request("GET", "cert", kwargs)

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

        """
        return self._request("GET", "cert/certificate_signing_request", kwargs)

    def set_certificate(self, **kwargs):
        """Create a self-signed certificate or imports a certificate signed
        by a certificate authority (CA).

        :param \*\*kwargs: See the REST API Guide on your array for the
                           documentation on the request:
                           **PUT cert**
        :type \*\*kwargs: optional

        :returns: A dictionary describing the configured array certificate.
        :rtype: ResponseDict

        .. note::

            Requires use of REST API 1.3 or later.

        """
        return self._request("PUT", "cert", kwargs)

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

    #
    # App management methods
    #

    def get_app(self, app):
        """Get app attributes.

        :param app: Name of app to get information about.
        :type app: str

        :returns: A dictionary describing app.
        :rtype: ResponseDict

        """
        return self._request("GET", "app/{0}".format(app))

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

