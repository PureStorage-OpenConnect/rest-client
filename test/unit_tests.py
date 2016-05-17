"""
Unit tests for REST Client
"""

import json
import mock
import requests

from pure.rest.client.purestorage import FlashArray, PureError, PureHTTPError

CLIENT_PATH = "pure.rest.client.purestorage.purestorage"
ARRAY_OBJ = CLIENT_PATH + ".FlashArray"

class TestBase(object):

    def setup_method(self, __):
        self.api_token = "12345678-abcd-1234-abcd-1234567890ab"
        self.api_token_data = {"api_token": self.api_token}
        self.other_rest_versions = ["0.1", "1.1", "1.0", "99.9"]
        self.rest_version = "1.2"
        self.rest_version_data = {"version": self.other_rest_versions}
        self.target = "pure-target"

    @staticmethod
    def assert_raises(error, func, *args, **kwargs):
        """Assert that a function raises the correct error

        Fail if the function, called with the specified args and kwargs
        doesn't raise an exception of type error.
        """
        try:
            func(*args, **kwargs)
        except error:
            pass
        else:
            raise AssertionError()

    @classmethod
    def assert_error_propagates(cls, mocks, func, *args, **kwargs):
        """Assert that errors from mocks propogate to func.

        Fail if exceptions raised by mocks are not seen when calling
        func(*args, **kwargs). Ensure that we are really seeing exceptions
        from the mocks by failing if just running func(*args, **kargs) raises
        an exception itself.
        """
        func(*args, **kwargs)
        for mock_func in mocks:
            mock_func.side_effect = PureError("reason")
            cls.assert_raises(PureError, func, *args, **kwargs)
            mock_func.side_effect = None


@mock.patch(ARRAY_OBJ + "._request", autospec=True)
class TestInit(TestBase):

    def setup_method(self, method):
        super(TestInit, self).setup_method(method)
        self.password = "purepass"
        self.username = "pureuser"
        self.username_data = {"username": self.username}

    def test_init_with_api_token(self, mock_request):
        mock_request.side_effect = iter([
            self.rest_version_data,
            self.username_data,
        ])
        array = FlashArray(self.target, api_token=self.api_token)
        expected = [
            mock.call(
                array, "GET", "https://{0}/api/api_version".format(self.target),
                reestablish_session=False),
            mock.call(array, "POST", "auth/session", self.api_token_data,
                      reestablish_session=False),
        ]
        assert array._target == self.target
        assert array._rest_version == "1.1"
        assert array._renegotiate_rest_version == True
        assert array._api_token == self.api_token
        assert mock_request.call_args_list == expected

    def test_init_with_username_password(self, mock_request):
        mock_request.side_effect = iter([
            self.rest_version_data,
            self.api_token_data,
            self.username_data,
        ])
        array = FlashArray(self.target, self.username, self.password)
        expected = [
            mock.call(array, "GET",
                      "https://{0}/api/api_version".format(self.target),
                      reestablish_session=False),
            mock.call(array, "POST", "auth/apitoken",
                      {"username": self.username, "password": self.password},
                      reestablish_session=False),
            mock.call(array, "POST", "auth/session", self.api_token_data,
                      reestablish_session=False),
        ]
        assert array._target == self.target
        assert array._rest_version == "1.1"
        assert array._renegotiate_rest_version == True
        assert array._api_token == self.api_token
        assert mock_request.call_args_list == expected

    def test_init_with_version(self, mock_request):
        mock_request.side_effect = iter([
            {"version": ["0.1", "1.1", "1.0", "1.2", "1.3"]},
            self.username_data,
        ])
        array = FlashArray(self.target, api_token=self.api_token, rest_version="1.0")
        expected = [
            mock.call(array, "GET",
                      "https://{0}/api/api_version".format(self.target),
                      reestablish_session=False),
            mock.call(array, "POST", "auth/session", self.api_token_data,
                      reestablish_session=False),
        ]
        assert array._target == self.target
        assert array._rest_version == "1.0"
        assert array._renegotiate_rest_version == False
        assert array._api_token == self.api_token
        assert mock_request.call_args_list == expected

    @mock.patch(ARRAY_OBJ + "._start_session", autospec=True)
    @mock.patch(ARRAY_OBJ + "._obtain_api_token", autospec=True)
    @mock.patch(ARRAY_OBJ + "._check_rest_version", autospec=True)
    @mock.patch(ARRAY_OBJ + "._choose_rest_version", autospec=True)
    def test_init_exceptions(self, mock_choose, mock_check, mock_obtain,
                             mock_start, __):
        mock_choose.return_value = self.rest_version
        mock_check.return_value = self.rest_version
        mock_obtain.return_value = self.api_token
        mock_start.return_value = None
        self.assert_error_propagates(
            [mock_choose, mock_start], FlashArray,
            self.target, api_token=self.api_token)
        self.assert_error_propagates(
            [mock_check, mock_start], FlashArray,
            self.target, api_token=self.api_token,
            rest_version=self.rest_version)
        self.assert_error_propagates(
            [mock_choose, mock_obtain, mock_start], FlashArray,
            self.target, self.username, self.password)
        self.assert_error_propagates(
            [mock_check, mock_obtain, mock_start], FlashArray,
            self.target, self.username, self.password,
            rest_version=self.rest_version)

    def test_init_bad_args(self, mock_request):
        args_list = [
            ([self.username, self.password], self.api_token_data),
            ([self.username], self.api_token_data),
            ([], {"api_token": self.api_token, "password": self.password}),
            ([self.username], {}),
            ([self.password], {}),
            ([], {}),
        ]
        for args, kwargs in args_list:
            self.assert_raises(ValueError, FlashArray, self.target, *args, **kwargs)
        assert mock_request.call_count == 0

    def test_init_verify_https(self, mock_request):
        mock_request.side_effect = iter([
            self.rest_version_data,
            self.username_data,
        ])
        cert_path = '/etc/ssl/certs/ca-cert.crt'
        array = FlashArray(self.target,
                           api_token=self.api_token,
                           verify_https=True,
                           ssl_cert=cert_path)
        expected = [
            mock.call(
                array, "GET", "https://{0}/api/api_version".format(self.target),
                reestablish_session=False),
            mock.call(array, "POST", "auth/session", self.api_token_data,
                      reestablish_session=False),
        ]

        mock_request.assert_has_calls(expected)
        assert cert_path == array._ssl_cert
        assert array._verify_https

class TestArrayBase(TestBase):

    def setup_method(self, method):
        super(TestArrayBase, self).setup_method(method)
        self.cookie_jar = {"session": "session-cookie"}
        self.supported_rest_versions = ["1.0", "1.1", "1.2"]

        array = FakeFlashArray()
        array.supported_rest_versions = self.supported_rest_versions
        array._target = self.target
        array._rest_version = self.rest_version
        array._renegotiate_rest_version = True
        array._api_token = self.api_token
        array._cookies = self.cookie_jar
        array._verify_https = False
        array._ssl_cert = None
        array._user_agent = None
        self.array = array


@mock.patch(CLIENT_PATH + ".requests.request", autospec=True)
class TestRequest(TestArrayBase):

    def setup_method(self, method):
        super(TestRequest, self).setup_method(method)
        self.method = "POST"
        self.path = "path"
        self.path_template = "https://{0}/api/{1}/{2}"
        self.full_path = self.path_template.format(
            self.target, self.rest_version, self.path)
        self.cookies = self.cookie_jar
        self.data = {"list": [1, 2, 3]}
        self.data_json = json.dumps(self.data)
        self.error_msg = "ERROR!"
        self.headers = {"Content-Type": "application/json"}
        self.new_cookies = {"session": "new-session-cookie"}
        self.response_json = '[{"hello": "world"}, "!"]'
        self.result = json.loads(self.response_json)

        self.ssl_cert = '/etc/ssl/certs/ca-cert.crt'
        self.default_call = self.make_call()

    def make_response(self, status, data=None, cookies=None):
        response = mock.Mock(
            spec=["reason", "status_code", "headers", "text", "json", "cookies"])
        response.cookies = cookies or {}
        response.headers = self.headers
        response.json.return_value = data or self.result
        response.reason = self.error_msg
        response.status_code = status
        response.text = json.dumps(self.response_json)
        return response

    def make_call(self, method=None, path=None, data=None, cookies=None, headers=None):
        method = method or self.method
        path = path or self.full_path
        data = data or self.data_json
        cookies = cookies or self.cookies
        headers = headers or self.headers
        return mock.call(method, path, data=data, headers=headers,
                         cookies=cookies, verify=False)

    def test_request_success(self, mock_request):
        mock_request.return_value = self.make_response(200)
        real_result = self.array._request(self.method, self.path, self.data)
        assert self.result == real_result
        assert mock_request.call_args_list == [self.default_call]

    def test_request_custom_user_agent_success(self, mock_request):
        mock_request.return_value = self.make_response(200)
        user_agent = 'Foo Client/3.2.1'
        headers = self.headers
        headers['User-Agent'] = user_agent
        self.array._user_agent = user_agent
        real_result = self.array._request(self.method, self.path, self.data)
        assert self.result == real_result
        assert mock_request.call_args_list == [self.make_call(headers=headers)]

    def test_request_401_error(self, mock_request):
        start_session_call = self.make_call(
            "POST", self.path_template.format(
                self.target, self.rest_version, "auth/session"),
            json.dumps(self.api_token_data))

        mock_request.side_effect = iter([
            self.make_response(401),
            self.make_response(200, cookies=self.new_cookies),
            self.make_response(200, cookies=self.new_cookies)
        ])
        real_result = self.array._request(self.method, self.path, self.data)
        assert self.result == real_result
        expected = [self.default_call,
                    start_session_call,
                    self.make_call(cookies=self.new_cookies)]
        assert mock_request.call_args_list == expected
        mock_request.reset_mock()

        mock_request.side_effect = iter([self.make_response(401)] * 2)
        expected = [self.default_call, start_session_call]
        self.assert_raises(PureHTTPError, self.array._request,
                           self.method, self.path, self.data)
        assert mock_request.call_args_list == expected
        mock_request.reset_mock()

        mock_request.side_effect = iter([
            self.make_response(401),
            self.make_response(200, cookies=self.new_cookies),
            self.make_response(401),
        ])
        expected = [self.default_call, start_session_call, self.make_call()]
        self.assert_raises(PureHTTPError, self.array._request,
                           self.method, self.path, self.data)
        assert mock_request.call_args_list == expected

    def test_request_450_error(self, mock_request):
        choose_rest_version_call = self.make_call(
            "GET", "https://{0}/api/api_version".format(self.target), "null")
        mock_request.side_effect = iter([
            self.make_response(450),
            self.make_response(200, self.rest_version_data),
            self.make_response(200),
        ])
        expected = [
            self.default_call,
            choose_rest_version_call,
            self.make_call(
                path=self.path_template.format(self.target, "1.1", self.path))
        ]
        real_result = self.array._request(self.method, self.path, self.data)
        assert self.result == real_result
        assert mock_request.call_args_list == expected
        mock_request.reset_mock()
        self.array._rest_version = self.rest_version

        mock_request.side_effect = iter([
            self.make_response(450),
            self.make_response(200, {"version": ["1.1", self.rest_version, "1.3"]}),
        ])
        expected = [self.default_call, choose_rest_version_call]
        self.assert_raises(PureHTTPError, self.array._request,
                           self.method, self.path, self.data)
        assert mock_request.call_args_list == expected
        mock_request.reset_mock()

        mock_request.side_effect = iter([
            self.make_response(450),
            PureError("reason")
        ])
        expected = [self.default_call, choose_rest_version_call]
        self.assert_raises(PureError, self.array._request,
                           self.method, self.path, self.data)
        assert mock_request.call_args_list == expected
        mock_request.reset_mock()

        self.array._renegotiate_rest_version = False
        mock_request.return_value = self.make_response(450)
        mock_request.side_effect = None
        expected = [self.default_call]
        self.assert_raises(PureHTTPError, self.array._request,
                           self.method, self.path, self.data)
        assert mock_request.call_args_list == expected

    def test_request_other_error(self, mock_request):
        mock_request.return_value = self.make_response(500)
        self.assert_raises(PureHTTPError, self.array._request,
                           self.method, self.path, self.data)
        assert mock_request.call_args_list == [self.default_call]

    def test_request_request_exception(self, mock_request):
        mock_request.side_effect = requests.exceptions.RequestException
        # try/except used to ensure is instance of type but not subtype
        try:
            self.array._request(self.method, self.path, self.data)
        except PureError as err:
            assert not isinstance(err, PureHTTPError)
        else:
            raise AssertionError()
        assert mock_request.call_args_list == [self.default_call]

    def test_request_other_exception(self, mock_request):
        mock_request.return_value = self.make_response(200)
        self.assert_error_propagates([mock_request], self.array._request,
                                     self.method, self.path, self.data)

    def _test_request_verify_https_with_ssl_cert(self, mock_request,
                                                 verify_https=False,
                                                 ssl_cert=None,
                                                 expected_verify=None):
        self.array._verify_https = verify_https
        self.array._ssl_cert = ssl_cert
        mock_request.return_value = self.make_response(200)
        self.array._request(self.method, self.path, self.data)
        mock_request.assert_called_once_with(self.method,
                                             self.full_path,
                                             headers=self.headers,
                                             cookies=self.cookies,
                                             data=self.data_json,
                                             verify=expected_verify)

    def test_request_verify_https(self, mock_request):
        self._test_request_verify_https_with_ssl_cert(mock_request,
                                                      verify_https=True,
                                                      expected_verify=True)

    def test_request_verify_https_with_ssl_cert(self, mock_request):
        self._test_request_verify_https_with_ssl_cert(mock_request,
                                                      verify_https=True,
                                                      ssl_cert=self.ssl_cert,
                                                      expected_verify=self.ssl_cert)

    def test_request_dont_verify_https_with_ssl_cert(self, mock_request):
        self._test_request_verify_https_with_ssl_cert(mock_request,
                                                      verify_https=False,
                                                      ssl_cert=self.ssl_cert,
                                                      expected_verify=False)


@mock.patch(ARRAY_OBJ + "._request", autospec=True)
class TestOtherMethods(TestArrayBase):

    def test_check_rest_version(self, mock_request):
        mock_request.return_value = self.rest_version_data
        ex_args = [self.array, "GET",
                    "https://{0}/api/api_version".format(self.target)]
        ex_kwargs = {"reestablish_session": False}
        result = self.array._check_rest_version("1.0")
        assert result == "1.0"
        mock_request.assert_called_once_with(*ex_args, **ex_kwargs)
        mock_request.reset_mock()

        result = self.array._check_rest_version(1.0)
        assert result == "1.0"
        mock_request.assert_called_once_with(*ex_args, **ex_kwargs)
        mock_request.reset_mock()

        self.assert_raises(ValueError, self.array._check_rest_version, "0.1")
        assert mock_request.call_count == 0
        mock_request.reset_mock()

        self.assert_raises(ValueError, self.array._check_rest_version, "1.2")
        mock_request.assert_called_once_with(*ex_args, **ex_kwargs)
        mock_request.reset_mock()

        mock_request.side_effect = PureError("reason")
        self.assert_raises(PureError, self.array._check_rest_version, "1.0")
        mock_request.assert_called_once_with(*ex_args, **ex_kwargs)

    def test_choose_rest_version(self, mock_request):
        mock_request.return_value = self.rest_version_data
        ex_args = [self.array, "GET",
                    "https://{0}/api/api_version".format(self.target)]
        ex_kwargs = {"reestablish_session": False}
        result = self.array._choose_rest_version()
        assert result == "1.1"
        mock_request.assert_called_once_with(*ex_args, **ex_kwargs)
        mock_request.reset_mock()

        mock_request.return_value = {"version": ["0.1", "1.3"]}
        self.assert_raises(PureError, self.array._choose_rest_version)
        mock_request.assert_called_once_with(*ex_args, **ex_kwargs)
        mock_request.reset_mock()

        mock_request.side_effect = PureError("reason")
        self.assert_raises(PureError, self.array._choose_rest_version)
        mock_request.assert_called_once_with(*ex_args, **ex_kwargs)


class FakeFlashArray(FlashArray):
    """FlashArray with dummy __init__ so attributes can be set directly"""
    def __init__(self):
        pass
