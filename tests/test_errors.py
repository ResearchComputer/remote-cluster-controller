from rcc.errors import ConfigError, MissingDependencyError, RccError, RemoteError


def test_all_errors_subclass_rcc_error():
    for cls in (ConfigError, RemoteError, MissingDependencyError):
        assert issubclass(cls, RccError)


def test_remote_error_carries_exit_code():
    err = RemoteError("rsync failed", exit_code=23)
    assert err.exit_code == 23
    assert "rsync failed" in str(err)


def test_remote_error_defaults_exit_code_to_one():
    assert RemoteError("boom").exit_code == 1
