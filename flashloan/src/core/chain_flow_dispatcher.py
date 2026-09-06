from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable

from core.chain_data_registry import (
    chain_config_summary,
    chain_flow_spec_by_chain_id,
    global_chain_id,
    processing_flow_for_chain,
)


class ChainFlowDispatchError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class ChainFlowRequest:
    flow_name: str
    protocol: str
    chain_id: int
    network: str
    global_chain_id: str
    handler: str
    flow: dict[str, Any]
    chain_config: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "flow_name": self.flow_name,
            "protocol": self.protocol,
            "chain_id": self.chain_id,
            "network": self.network,
            "global_chain_id": self.global_chain_id,
            "chain_key": self.global_chain_id,
            "handler": self.handler,
            "allowed_protocols": list(self.flow.get("allowed_protocols") or []),
            "data_file_keys": list(self.flow.get("data_file_keys") or []),
            "requires_scope_match": self.flow.get("requires_scope_match") is True,
        }


def _load_handler(handler_path: str) -> Callable[..., dict[str, Any]]:
    module_name, sep, attr_name = str(handler_path or "").partition(":")
    if not sep or not module_name or not attr_name:
        raise ChainFlowDispatchError("invalid_chain_flow_handler", f"invalid chain flow handler: {handler_path}")
    module = import_module(module_name)
    handler = getattr(module, attr_name, None)
    if not callable(handler):
        raise ChainFlowDispatchError("chain_flow_handler_missing", f"chain flow handler is not callable: {handler_path}")
    return handler


def chain_flow_request(
    *,
    chain_id: int | None,
    flow_name: str,
    protocol: str = "",
) -> ChainFlowRequest:
    if chain_id is None:
        raise ChainFlowDispatchError("chain_scope_required", f"{flow_name} requires a concrete chain_id")
    flow = processing_flow_for_chain(int(chain_id), flow_name)
    if not flow:
        raise ChainFlowDispatchError("chain_flow_missing", f"chain {chain_id} does not declare flow {flow_name}")
    handler_path = str(flow.get("handler") or "").strip()
    if not handler_path:
        raise ChainFlowDispatchError("chain_flow_handler_missing", f"chain {chain_id} flow {flow_name} has no handler")
    spec = chain_flow_spec_by_chain_id(int(chain_id)) or {}
    network = str(spec.get("network") or "").strip().lower()
    gid = global_chain_id(chain_id=int(chain_id), network=network)
    return ChainFlowRequest(
        flow_name=str(flow_name),
        protocol=str(protocol or "").strip().lower(),
        chain_id=int(chain_id),
        network=network,
        global_chain_id=gid,
        handler=handler_path,
        flow=flow,
        chain_config=chain_config_summary(chain_id=int(chain_id)),
    )


def dispatch_chain_flow(
    *,
    chain_id: int | None,
    flow_name: str,
    protocol: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    request = chain_flow_request(chain_id=chain_id, flow_name=flow_name, protocol=protocol)
    allowed_protocols = {str(item or "").strip().lower() for item in request.flow.get("allowed_protocols") or []}
    if allowed_protocols and request.protocol and request.protocol not in allowed_protocols:
        raise ChainFlowDispatchError(
            "protocol_not_allowed_for_chain_flow",
            f"{request.global_chain_id} flow {flow_name} does not allow protocol {request.protocol}",
        )
    handler = _load_handler(request.handler)
    result = handler(request=request, **kwargs)
    if not isinstance(result, dict):
        raise ChainFlowDispatchError("chain_flow_invalid_result", f"{request.handler} must return a dict")
    processing = dict(result.get("processing") if isinstance(result.get("processing"), dict) else {})
    processing.update(request.as_dict())
    return {**result, "processing": processing}


__all__ = [
    "ChainFlowDispatchError",
    "ChainFlowRequest",
    "chain_flow_request",
    "dispatch_chain_flow",
]
