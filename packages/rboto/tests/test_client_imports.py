"""Public import and constructor contracts without requiring installed native wheels."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from rboto_codegen.model import ServiceDescriptor
from rboto_codegen.registry import list_services

ROOT = Path(__file__).resolve().parents[3]


def run_isolated(source: str, *args: str) -> None:
    result = subprocess.run([sys.executable, "-c", source, *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_core_import_and_discovery_do_not_load_services() -> None:
    names = [service.client_class for service in list_services()]
    run_isolated(
        """
import importlib.abc
import json
import sys

class RejectServices(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("rboto_"):
            raise AssertionError("Unexpected service import: " + fullname)

sys.meta_path.insert(0, RejectServices())
import rboto
assert set(json.loads(sys.argv[1])) <= set(dir(rboto))
assert not any(name.startswith("rboto_") for name in sys.modules)
try:
    getattr(rboto, "NotAClient")
except AttributeError:
    pass
else:
    raise AssertionError("Unknown attributes must not resolve")
""",
        json.dumps(names),
    )


@pytest.mark.parametrize("service", list_services(), ids=lambda item: item.service_id)
def test_canonical_class_aliases_factories_and_sessions(service: ServiceDescriptor) -> None:
    run_isolated(
        """
import importlib
import importlib.abc
import inspect
import json
import sys
from types import ModuleType

package, name, legacy, factory_name, source_path, service_id = json.loads(sys.argv[1])
sys.path.insert(0, source_path)

class RejectOtherServices(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("rboto_") and fullname.split(".")[0] != package:
            raise AssertionError("Unexpected service import: " + fullname)

sys.meta_path.insert(0, RejectOtherServices())
native = ModuleType(package + "._native")
def initialize(self, **kwargs):
    self.arguments = kwargs
def native_attribute(attribute):
    if attribute.startswith("__"):
        raise AttributeError(attribute)
    value = type(attribute, (), {"__init__": initialize})
    setattr(native, attribute, value)
    return value
native.__getattr__ = native_attribute
sys.modules[native.__name__] = native

import rboto
assert package not in sys.modules
namespace = {}
exec(f"from rboto import {name}", namespace)
cls = namespace[name]
service = importlib.import_module(package)
assert cls is getattr(service, name)
assert cls.__name__ == name
assert cls.__module__ == package + ".client"
assert getattr(service, legacy) is cls
assert getattr(importlib.import_module(package + ".client"), legacy) is cls
assert getattr(rboto, name) is cls
assert cls.logger_name == "rboto." + service_id
assert rboto.get_logger(service_id).name == cls.logger_name

config = rboto.ClientConfig(max_attempts=2, proxy_url="http://localhost:8080")
session = rboto.Session(
    region="us-east-1", profile="test", endpoint_url="http://localhost:4566", config=config,
)
expected = {
    "region": session.region, "profile": session.profile, "endpoint_url": session.endpoint_url,
    "config": config,
}
factory = getattr(rboto, factory_name)
for constructor in (cls, factory):
    client = constructor(session=session)
    assert type(client) is cls
    assert client._native.arguments == expected
    assert constructor(region="eu-west-1")._native.arguments["region"] == "eu-west-1"
    assert constructor(config=config)._native.arguments["config"] is config
    try:
        constructor(session=session, config=config)
    except ValueError:
        pass
    else:
        raise AssertionError("Ambiguous ClientConfig accepted")
    for keyword in ("region", "profile", "endpoint_url"):
        try:
            constructor(session=session, **{keyword: "conflict"})
        except ValueError:
            pass
        else:
            raise AssertionError("Ambiguous configuration accepted")
assert all(
    parameter.kind is inspect.Parameter.KEYWORD_ONLY
    for parameter in inspect.signature(cls).parameters.values()
)

# The same client picks up global logging configured after construction.
import asyncio
import io
stream = io.StringIO()
client = cls(region="us-east-1")
class Spy:
    def __getattr__(self, name):
        async def call(params, metadata=None):
            return object()
        return call
client._native = Spy()
method_name = next(
    name for name, method in cls.__dict__.items() if inspect.iscoroutinefunction(method)
)
method = getattr(client, method_name)
parameters = {
    name: object() for name, parameter in inspect.signature(method).parameters.items()
    if parameter.default is inspect.Parameter.empty
}
rboto.configure_logging(stream=stream)
asyncio.run(method(**parameters))
record = json.loads(stream.getvalue())
assert record["logger"] == cls.logger_name
assert record["service"] == service_id
rboto.get_logger(service_id).disabled = True
asyncio.run(method(**parameters))
assert len(stream.getvalue().splitlines()) == 1
""",
        json.dumps(
            [
                service.python_package,
                service.client_class,
                service.native_class,
                service.python_name,
                str(ROOT / "packages" / service.distribution_name / "python"),
                service.service_id,
            ]
        ),
    )


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("rboto_s3", 'pip install "rboto[s3]"'),
        ("rboto_s3._native", "original import failure"),
        ("transitive_dependency", "original import failure"),
        ("", "original import failure"),
    ],
)
def test_missing_service_hint_does_not_hide_broken_dependencies(missing: str, message: str) -> None:
    run_isolated(
        """
import importlib.abc
import sys

missing, message = sys.argv[1:]
class FailS3(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "rboto_s3":
            if missing:
                raise ModuleNotFoundError("original import failure", name=missing)
            raise ImportError("original import failure")

sys.meta_path.insert(0, FailS3())
import rboto
for operation in (lambda: getattr(rboto, "S3Client"), rboto.s3):
    try:
        operation()
    except ImportError as error:
        assert message in str(error), str(error)
        if missing != "rboto_s3":
            assert "pip install" not in str(error)
    else:
        raise AssertionError("Missing import error")
assert "S3Client" not in vars(rboto)
""",
        missing,
        message,
    )
