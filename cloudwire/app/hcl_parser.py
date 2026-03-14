"""Parse Terraform .tf (HCL) files into CloudWire graph nodes and edges.

Unlike .tfstate which contains deployed infrastructure with real ARNs,
.tf files contain *declared* infrastructure with reference expressions.
This parser extracts resource blocks as nodes and HCL references as edges,
producing a "planned architecture" graph.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .graph_store import GraphStore
from .terraform_parser import (
    TF_RESOURCE_TYPE_MAP,
    _is_sensitive_key,
    _redact_sensitive,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex to find Terraform resource references in HCL attribute values.
# Matches patterns like: aws_sqs_queue.orders, aws_lambda_function.handler
_HCL_REF_PATTERN = re.compile(
    r"\b((?:aws_[a-z0-9_]+|random_[a-z0-9_]+)\.[a-z_][a-z0-9_]*)\b"
)

# Depth/element limits for recursive value scanning (DoS prevention).
_MAX_SCAN_DEPTH = 16
_MAX_SCAN_ELEMENTS = 2048


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unwrap_hcl2(value: Any) -> Any:
    """python-hcl2 wraps most values in single-element lists. Unwrap them.

    Example: {"name": ["my-func"]} → {"name": "my-func"}
    """
    if isinstance(value, list):
        if len(value) == 1:
            return _unwrap_hcl2(value[0])
        return [_unwrap_hcl2(item) for item in value]
    if isinstance(value, dict):
        return {k: _unwrap_hcl2(v) for k, v in value.items()}
    return value


def _extract_refs_from_value(
    value: Any,
    _depth: int = 0,
    _counter: Optional[List[int]] = None,
) -> List[str]:
    """Recursively extract TYPE.NAME reference strings from HCL attribute values."""
    if _counter is None:
        _counter = [0]
    refs: List[str] = []
    if _depth > _MAX_SCAN_DEPTH:
        return refs
    _counter[0] += 1
    if _counter[0] > _MAX_SCAN_ELEMENTS:
        return refs
    if isinstance(value, str):
        refs.extend(_HCL_REF_PATTERN.findall(value))
    elif isinstance(value, dict):
        for v in value.values():
            refs.extend(_extract_refs_from_value(v, _depth + 1, _counter))
            if _counter[0] > _MAX_SCAN_ELEMENTS:
                break
    elif isinstance(value, list):
        for item in value:
            refs.extend(_extract_refs_from_value(item, _depth + 1, _counter))
            if _counter[0] > _MAX_SCAN_ELEMENTS:
                break
    return refs


def _hcl_label(tf_type: str, tf_name: str, body: Dict[str, Any]) -> str:
    """Pick the best human-readable label for an HCL resource."""
    for key in (
        "name", "function_name", "table_name", "bucket", "cluster_name",
        "cluster_identifier", "domain_name", "queue_name", "topic_name",
        "api_name", "state_machine_name", "repository_name",
    ):
        val = body.get(key)
        if isinstance(val, str) and val:
            # HCL values often contain interpolations like ${var.prefix}-handler
            # Still useful as labels
            return val
    return tf_name


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_hcl_content(raw: bytes, filename: str) -> Dict[str, Any]:
    """Parse raw bytes as HCL and return the parsed dict.

    Raises ValueError with a user-friendly message on failure.
    """
    try:
        import hcl2
    except ImportError:
        raise ValueError(
            "HCL file support requires 'python-hcl2'. "
            "Install with: pip install python-hcl2"
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"File '{filename}' is not valid UTF-8 text.")

    try:
        parsed = hcl2.load(io.StringIO(text))
    except Exception:
        raise ValueError(f"File '{filename}' contains invalid HCL syntax.")

    if not isinstance(parsed, dict):
        raise ValueError(f"File '{filename}' did not parse to a valid HCL structure.")

    return parsed


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class HCLParser:
    """Parse one or more .tf (HCL) dicts into a GraphStore."""

    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self.warnings: List[str] = []
        self._declared_resources: Dict[str, str] = {}  # "TYPE.NAME" → node_id
        self._node_bodies: Dict[str, Dict[str, Any]] = {}  # node_id → unwrapped body
        self._unknown_types: Set[str] = set()
        self._redacted_count = 0

    def parse(self, hcl_dicts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Two-pass parse: register nodes, then infer edges from references."""

        # Pass 1: Extract resource blocks as nodes.
        for hcl_data in hcl_dicts:
            try:
                self._register_resources(hcl_data)
            except Exception as exc:
                logger.warning("Error registering HCL resources: %s", exc)
                self.warnings.append(f"Skipped some resources due to parse error: {exc}")

        # Pass 2: Scan all attribute values for resource references → edges.
        for node_id, body in self._node_bodies.items():
            try:
                self._infer_edges(node_id, body)
            except Exception as exc:
                logger.warning("Error inferring HCL edges for %s: %s", node_id, exc)

        # Warnings for unmapped types
        if self._unknown_types:
            sample = sorted(self._unknown_types)[:5]
            extra = len(self._unknown_types) - len(sample)
            msg = f"Unmapped HCL resource types (shown as generic): {', '.join(sample)}"
            if extra > 0:
                msg += f" and {extra} more"
            self.warnings.append(msg)

        if self._redacted_count > 0:
            self.warnings.append(
                f"Redacted {self._redacted_count} sensitive attribute(s) from HCL resources."
            )

        payload = self.store.get_graph_payload()
        metadata = payload.get("metadata", {})
        return {
            "resource_count": metadata.get("node_count", 0),
            "edge_count": metadata.get("edge_count", 0),
            "file_count": len(hcl_dicts),
            "warnings": self.warnings,
        }

    # ------------------------------------------------------------------
    # Pass 1: Node registration
    # ------------------------------------------------------------------

    def _register_resources(self, hcl_data: Dict[str, Any]) -> None:
        """Extract resource blocks from a parsed HCL dict.

        python-hcl2 output format:
        {"resource": [{"aws_lambda_function": {"handler": [{...body...}]}}]}
        All values are wrapped in lists.
        """
        resource_blocks = hcl_data.get("resource")
        if not resource_blocks:
            return

        if not isinstance(resource_blocks, list):
            resource_blocks = [resource_blocks]

        for block in resource_blocks:
            if not isinstance(block, dict):
                continue
            for tf_type, name_map_raw in block.items():
                # name_map_raw might be a dict or a list containing a dict
                name_map = name_map_raw
                if isinstance(name_map, list):
                    # python-hcl2 wraps in list
                    if len(name_map) == 1:
                        name_map = name_map[0]
                    else:
                        # Multiple resources of same type — each list item is a name_map
                        for item in name_map:
                            if isinstance(item, dict):
                                self._process_name_map(tf_type, item)
                        continue
                if isinstance(name_map, dict):
                    self._process_name_map(tf_type, name_map)

    def _process_name_map(self, tf_type: str, name_map: Dict[str, Any]) -> None:
        """Process a {tf_name: body} dict for a given tf_type."""
        for tf_name, body_raw in name_map.items():
            # body_raw is typically [{...}] (list-wrapped dict) or {...}
            body = _unwrap_hcl2(body_raw)
            if isinstance(body, list) and body and isinstance(body[0], dict):
                body = body[0]
            if not isinstance(body, dict):
                body = {}
            self._register_single_resource(tf_type, tf_name, body)

    def _register_single_resource(
        self, tf_type: str, tf_name: str, body: Dict[str, Any],
    ) -> None:
        ref_key = f"{tf_type}.{tf_name}"
        node_id = f"terraform:{ref_key}"

        # Skip if already registered (from a different .tf file)
        if ref_key in self._declared_resources:
            return

        # Determine service mapping
        mapping = TF_RESOURCE_TYPE_MAP.get(tf_type)
        if mapping:
            service, node_type = mapping
        else:
            self._unknown_types.add(tf_type)
            service = "terraform"
            node_type = tf_type

        label = _hcl_label(tf_type, tf_name, body)

        # Redact sensitive attrs
        safe_body = _redact_sensitive(body)
        self._redacted_count += len(body) - len(safe_body)

        # Only store simple scalar attrs on the node (avoid nested dicts/lists as kwargs)
        scalar_attrs = {}
        for k, v in safe_body.items():
            if isinstance(v, (str, int, float, bool)):
                scalar_attrs[k] = v

        self.store.add_node(
            node_id,
            label=label,
            service=service,
            type=node_type,
            tf_type=tf_type,
            tf_name=tf_name,
            tf_address=ref_key,
            source="terraform_hcl",
            **scalar_attrs,
        )

        self._declared_resources[ref_key] = node_id
        self._node_bodies[node_id] = body  # full body for reference scanning

    # ------------------------------------------------------------------
    # Pass 2: Edge inference from HCL references
    # ------------------------------------------------------------------

    def _infer_edges(self, node_id: str, body: Dict[str, Any]) -> None:
        """Scan all attribute values for TYPE.NAME references to other declared resources."""
        refs = _extract_refs_from_value(body)
        seen: Set[str] = set()
        for ref_key in refs:
            if ref_key in seen:
                continue
            seen.add(ref_key)
            target_id = self._declared_resources.get(ref_key)
            if target_id and target_id != node_id:
                relationship = self._classify_relationship(ref_key)
                self.store.add_edge(
                    node_id, target_id,
                    relationship=relationship,
                    via="hcl_ref",
                )

    def _classify_relationship(self, ref_key: str) -> str:
        """Derive a relationship label from the referenced resource type."""
        tf_type = ref_key.split(".")[0] if "." in ref_key else ""
        if tf_type == "aws_iam_role":
            return "assumes"
        if tf_type.startswith("aws_iam_"):
            return "iam"
        if tf_type in ("aws_subnet", "aws_security_group", "aws_vpc"):
            return "network"
        if tf_type in ("aws_sqs_queue", "aws_sns_topic", "aws_kinesis_stream"):
            return "publishes_to"
        if tf_type in ("aws_dynamodb_table", "aws_s3_bucket", "aws_rds_cluster", "aws_db_instance"):
            return "reads_writes"
        return "references"
