"""
tests/test_proto_contract.py
────────────────────────────
Guards the gRPC contract: the generated stubs must import, expose the
MemoryService, and stay in sync with memory.proto (every `rpc` and every
top-level `message` in the .proto must have a counterpart in the stubs).
"""

from __future__ import annotations

import re
from pathlib import Path

PROTO = Path(__file__).resolve().parent.parent / "proto" / "memory.proto"


def test_generated_stubs_import():
    from proto import memory_pb2, memory_pb2_grpc
    assert hasattr(memory_pb2_grpc, "MemoryServiceServicer")
    assert hasattr(memory_pb2_grpc, "MemoryServiceStub")


def test_every_proto_message_has_python_class():
    from proto import memory_pb2
    text = PROTO.read_text()
    messages = re.findall(r"^\s*message\s+(\w+)", text, re.M)
    assert messages, "no messages found in memory.proto"
    for msg in messages:
        assert hasattr(memory_pb2, msg), f"missing generated class for message {msg}"


def test_every_proto_rpc_has_servicer_method():
    from proto import memory_pb2_grpc
    text = PROTO.read_text()
    rpcs = re.findall(r"^\s*rpc\s+(\w+)", text, re.M)
    assert rpcs, "no rpcs found in memory.proto"
    servicer_methods = set(dir(memory_pb2_grpc.MemoryServiceServicer))
    for rpc in rpcs:
        assert rpc in servicer_methods, f"servicer missing rpc {rpc}"
