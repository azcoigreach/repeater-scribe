import warnings

import conftest
import pytest

PORTAL_WARNING = (
    "The anyio.abc.BlockingPortal alias is deprecated, "
    "use anyio.from_thread.BlockingPortal instead."
)


@pytest.mark.parametrize(
    ("message", "module", "category", "allowed"),
    [
        (PORTAL_WARNING, "starlette.testclient", DeprecationWarning, True),
        ("Another deprecation", "starlette.testclient", DeprecationWarning, False),
        (PORTAL_WARNING, "asl_transcriber.main", DeprecationWarning, False),
        (PORTAL_WARNING, "starlette.testclient", UserWarning, False),
    ],
)
def test_testclient_import_warning_exception_is_scoped(
    monkeypatch, message, module, category, allowed
):
    def import_with_warning(name):
        assert name == "starlette.testclient"
        warnings.warn_explicit(message, category, filename="dependency.py", lineno=1, module=module)

    monkeypatch.setattr(conftest.importlib, "import_module", import_with_warning)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        original_filters = warnings.filters[:]
        if allowed:
            conftest._import_starlette_testclient()
        else:
            with pytest.raises(category):
                conftest._import_starlette_testclient()
        assert warnings.filters == original_filters
        # Even the known warning must fail again after the import has finished.
        with pytest.raises(DeprecationWarning):
            warnings.warn_explicit(
                PORTAL_WARNING,
                DeprecationWarning,
                filename="dependency.py",
                lineno=1,
                module="starlette.testclient",
            )
