"""Parse Terraform .tfstate (v4) files into CloudWire graph nodes and edges."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .graph_store import GraphStore
from .scanners._utils import _ARN_PATTERN, _ENV_VAR_CONVENTIONS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILES = 10
MAX_BYTES_PER_FILE = 25 * 1024 * 1024  # 25 MB
MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MB
# Hard cap on the number of resource entries accepted from a single state file.
# Prevents DoS via a crafted JSON file with a minimal per-entry byte footprint
# (e.g. {"resources":[{"mode":"managed","type":"aws_s3_bucket","name":"x","instances":[]}]}
#  repeated hundreds of thousands of times fits well within the 25 MB byte cap).
MAX_RESOURCES_PER_FILE = 10_000

# Attributes that must never be stored on graph nodes.
_SENSITIVE_EXACT: Set[str] = {
    "password", "master_password", "db_password",
    "auth_token", "token", "access_token",
    "secret_string", "secret_binary",
    "access_key", "secret_key", "secret_access_key",
    "private_key", "certificate_pem", "private_key_pem",
    "credentials", "connection_string",
}

_SENSITIVE_SUBSTRINGS = ("password", "secret", "private_key", "token", "credential", "cert")

# Terraform resource type prefix  →  (cloudwire_service, node_type)
TF_RESOURCE_TYPE_MAP: Dict[str, Tuple[str, str]] = {
    # Compute
    "aws_lambda_function": ("lambda", "lambda"),
    "aws_lambda_event_source_mapping": ("lambda", "event_source_mapping"),
    "aws_lambda_permission": ("lambda", "permission"),
    "aws_instance": ("ec2", "instance"),
    "aws_autoscaling_group": ("ec2", "autoscaling_group"),
    "aws_launch_template": ("ec2", "launch_template"),
    "aws_ecs_cluster": ("ecs", "cluster"),
    "aws_ecs_service": ("ecs", "service"),
    "aws_ecs_task_definition": ("ecs", "task_definition"),
    "aws_eks_cluster": ("eks", "cluster"),
    "aws_sfn_state_machine": ("stepfunctions", "state_machine"),
    "aws_glue_job": ("glue", "job"),
    "aws_glue_crawler": ("glue", "crawler"),
    "aws_glue_catalog_database": ("glue", "catalog_database"),
    "aws_batch_job_definition": ("batch", "job_definition"),
    "aws_batch_compute_environment": ("batch", "compute_environment"),
    "aws_elastic_beanstalk_environment": ("elasticbeanstalk", "environment"),
    "aws_emr_cluster": ("emr", "cluster"),
    # API & Integration
    "aws_api_gateway_rest_api": ("apigateway", "rest_api"),
    "aws_api_gateway_stage": ("apigateway", "stage"),
    "aws_api_gateway_integration": ("apigateway", "integration"),
    "aws_api_gateway_resource": ("apigateway", "resource"),
    "aws_api_gateway_method": ("apigateway", "method"),
    "aws_apigatewayv2_api": ("apigateway", "http_api"),
    "aws_apigatewayv2_integration": ("apigateway", "integration"),
    "aws_apigatewayv2_stage": ("apigateway", "stage"),
    "aws_appsync_graphql_api": ("appsync", "graphql_api"),
    "aws_mq_broker": ("mq", "broker"),
    # Queues & Streams
    "aws_sqs_queue": ("sqs", "queue"),
    "aws_sns_topic": ("sns", "topic"),
    "aws_sns_topic_subscription": ("sns", "subscription"),
    "aws_kinesis_stream": ("kinesis", "stream"),
    "aws_kinesis_firehose_delivery_stream": ("firehose", "delivery_stream"),
    "aws_msk_cluster": ("kafka", "cluster"),
    # EventBridge
    "aws_cloudwatch_event_rule": ("eventbridge", "rule"),
    "aws_cloudwatch_event_target": ("eventbridge", "target"),
    "aws_cloudwatch_event_bus": ("eventbridge", "event_bus"),
    # Database & Storage
    "aws_dynamodb_table": ("dynamodb", "table"),
    "aws_s3_bucket": ("s3", "bucket"),
    "aws_s3_bucket_notification": ("s3", "notification"),
    "aws_rds_cluster": ("rds", "cluster"),
    "aws_db_instance": ("rds", "instance"),
    "aws_rds_cluster_instance": ("rds", "cluster_instance"),
    "aws_elasticache_cluster": ("elasticache", "cluster"),
    "aws_elasticache_replication_group": ("elasticache", "replication_group"),
    "aws_redshift_cluster": ("redshift", "cluster"),
    "aws_opensearch_domain": ("opensearch", "domain"),
    "aws_elasticsearch_domain": ("opensearch", "domain"),
    "aws_efs_file_system": ("efs", "file_system"),
    "aws_ecr_repository": ("ecr", "repository"),
    # Networking
    "aws_vpc": ("vpc", "vpc"),
    "aws_subnet": ("vpc", "subnet"),
    "aws_security_group": ("vpc", "security_group"),
    "aws_internet_gateway": ("vpc", "internet_gateway"),
    "aws_nat_gateway": ("vpc", "nat_gateway"),
    "aws_route_table": ("vpc", "route_table"),
    "aws_lb": ("elb", "load_balancer"),
    "aws_alb": ("elb", "load_balancer"),
    "aws_lb_listener": ("elb", "listener"),
    "aws_lb_target_group": ("elb", "target_group"),
    "aws_lb_target_group_attachment": ("elb", "target_group_attachment"),
    "aws_cloudfront_distribution": ("cloudfront", "distribution"),
    "aws_route53_record": ("route53", "record"),
    "aws_route53_zone": ("route53", "zone"),
    "aws_acm_certificate": ("acm", "certificate"),
    # Security & Identity
    "aws_iam_role": ("iam", "role"),
    "aws_iam_policy": ("iam", "policy"),
    "aws_iam_role_policy": ("iam", "inline_policy"),
    "aws_iam_role_policy_attachment": ("iam", "policy_attachment"),
    "aws_cognito_user_pool": ("cognito", "user_pool"),
    "aws_cognito_user_pool_client": ("cognito", "client"),
    "aws_secretsmanager_secret": ("secretsmanager", "secret"),
    "aws_kms_key": ("kms", "key"),
    "aws_kms_alias": ("kms", "alias"),
    "aws_wafv2_web_acl": ("wafv2", "web_acl"),
    "aws_guardduty_detector": ("guardduty", "detector"),
    # Monitoring & Mgmt
    "aws_cloudwatch_log_group": ("cloudwatch", "log_group"),
    "aws_cloudwatch_metric_alarm": ("cloudwatch", "alarm"),
    "aws_cloudformation_stack": ("cloudformation", "stack"),
    "aws_cloudtrail": ("cloudtrail", "trail"),
    # Analytics & ML
    "aws_athena_workgroup": ("athena", "workgroup"),
    "aws_sagemaker_endpoint": ("sagemaker", "endpoint"),
    # Developer Tools
    "aws_codepipeline": ("codepipeline", "pipeline"),
    "aws_codebuild_project": ("codebuild", "project"),
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_sensitive_key(key: str) -> bool:
    low = key.lower()
    if low in _SENSITIVE_EXACT:
        return True
    return any(sub in low for sub in _SENSITIVE_SUBSTRINGS)


def _redact_sensitive(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *attrs* with sensitive keys removed."""
    return {k: v for k, v in attrs.items() if not _is_sensitive_key(k)}


_MAX_ARN_EXTRACT_DEPTH = 16
_MAX_ARN_EXTRACT_ELEMENTS = 2048


def _extract_arns_from_value(
    value: Any,
    _depth: int = 0,
    _counter: Optional[List[int]] = None,
) -> List[str]:
    """Recursively extract ARN strings from an arbitrary JSON value.

    Bounded by depth and total element count to prevent DoS via deeply nested
    or pathologically large JSON structures in untrusted .tfstate uploads.
    """
    if _counter is None:
        _counter = [0]
    arns: List[str] = []
    if _depth > _MAX_ARN_EXTRACT_DEPTH:
        return arns
    _counter[0] += 1
    if _counter[0] > _MAX_ARN_EXTRACT_ELEMENTS:
        return arns
    if isinstance(value, str):
        if _ARN_PATTERN.match(value):
            arns.append(value)
    elif isinstance(value, list):
        for item in value:
            arns.extend(_extract_arns_from_value(item, _depth + 1, _counter))
            if _counter[0] > _MAX_ARN_EXTRACT_ELEMENTS:
                break
    elif isinstance(value, dict):
        for v in value.values():
            arns.extend(_extract_arns_from_value(v, _depth + 1, _counter))
            if _counter[0] > _MAX_ARN_EXTRACT_ELEMENTS:
                break
    return arns


def _get_nested(data: Dict[str, Any], *keys: str) -> Any:
    """Safe nested dict/list access."""
    current: Any = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and current:
            current = current[0] if key == "0" else None
        else:
            return None
        if current is None:
            return None
    return current


def _service_from_arn(arn: str) -> Optional[str]:
    """Extract the AWS service segment from an ARN."""
    parts = arn.split(":")
    if len(parts) >= 3:
        return parts[2]
    return None


def _label_for_resource(
    tf_type: str, tf_name: str, attrs: Dict[str, Any],
) -> str:
    """Pick the best human-readable label for a resource."""
    # Try common name attributes in priority order
    for key in (
        "name", "function_name", "table_name", "bucket", "cluster_name",
        "cluster_identifier", "domain_name", "queue_name", "topic_name",
        "api_name", "state_machine_name", "repository_name",
    ):
        val = attrs.get(key)
        if val and isinstance(val, str):
            return val
    # Fall back to the Terraform logical name
    return tf_name


def _tf_address(resource: Dict[str, Any]) -> str:
    """Build the full Terraform address like module.api.aws_lambda_function.handler."""
    module = resource.get("module", "")
    prefix = f"{module}." if module else ""
    return f"{prefix}{resource['type']}.{resource['name']}"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TerraformParser:
    """Parse one or more .tfstate dicts into a GraphStore."""

    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self.warnings: List[str] = []
        self._arn_to_node: Dict[str, str] = {}
        self._address_to_node: Dict[str, List[str]] = {}  # address → [node_id, ...]
        self._node_attrs: Dict[str, Dict[str, Any]] = {}  # node_id → redacted attrs
        self._node_service: Dict[str, str] = {}  # node_id → service
        self._node_tf_type: Dict[str, str] = {}  # node_id → tf resource type
        self._redacted_count = 0
        self._unknown_types: Set[str] = set()
        # Secondary indices for O(1) edge lookups — populated during pass 1.
        # These avoid O(N²) scans in _edges_* methods.
        self._vpc_resource_id_to_node: Dict[str, str] = {}        # AWS resource id → node_id (vpc service)
        self._s3_bucket_name_to_node: Dict[str, str] = {}         # bucket name → node_id
        self._ecs_cluster_arn_to_node: Dict[str, str] = {}        # cluster ARN → node_id
        self._apigw_id_to_node: Dict[str, str] = {}               # API GW id → node_id
        self._iam_role_name_to_node: Dict[str, str] = {}          # IAM role name → node_id
        self._eventbridge_rule_name_to_node: Dict[str, str] = {}  # EB rule name → node_id
        self._node_label: Dict[str, str] = {}                     # node_id → computed label

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, state_dicts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Parse state files and populate the graph store.

        Returns a summary dict with resource_count, edge_count, warnings.
        """
        all_resources: List[Dict[str, Any]] = []
        file_count = 0
        for state in state_dicts:
            version = state.get("version")
            if version and version < 4:
                self.warnings.append(
                    f"State file version {version} is unsupported (need v4+). Skipping."
                )
                continue
            resources = state.get("resources", [])
            all_resources.extend(resources)
            file_count += 1

        # Pass 1: register nodes
        for resource in all_resources:
            if resource.get("mode") != "managed":
                continue
            if not resource.get("type", "").startswith("aws_"):
                continue
            self._register_resource(resource)

        # Pass 2: infer edges
        for resource in all_resources:
            if resource.get("mode") != "managed":
                continue
            if not resource.get("type", "").startswith("aws_"):
                continue
            self._infer_edges(resource)

        # Finalize metadata
        if self._unknown_types:
            sorted_types = sorted(self._unknown_types)
            preview = ", ".join(sorted_types[:5])
            suffix = f" and {len(sorted_types) - 5} more" if len(sorted_types) > 5 else ""
            self.warnings.append(
                f"{len(sorted_types)} unmapped resource type(s): {preview}{suffix}"
            )
        if self._redacted_count:
            self.warnings.append(
                f"Redacted {self._redacted_count} sensitive attribute(s) from node data."
            )

        graph = self.store.get_graph_payload()
        services = sorted({s for s in self._node_service.values()})
        self.store.update_metadata(
            source="terraform",
            scanned_services=services,
            warnings=self.warnings,
        )

        return {
            "resource_count": len(self._node_attrs),
            "edge_count": graph["metadata"]["edge_count"],
            "file_count": file_count,
            "warnings": self.warnings,
        }

    # ------------------------------------------------------------------
    # Pass 1: Node registration
    # ------------------------------------------------------------------

    def _register_resource(self, resource: Dict[str, Any]) -> None:
        tf_type = resource["type"]
        tf_name = resource["name"]
        address = _tf_address(resource)
        mapping = TF_RESOURCE_TYPE_MAP.get(tf_type)

        if mapping:
            service, node_type = mapping
        else:
            self._unknown_types.add(tf_type)
            service = "terraform"
            node_type = tf_type

        instances = resource.get("instances", [])
        for idx, instance in enumerate(instances):
            attrs = instance.get("attributes") or {}
            index_key = instance.get("index_key")

            # Build node ID — prefer real ARN for compatibility with live scans
            arn = attrs.get("arn") or attrs.get("id", "")
            if arn and _ARN_PATTERN.match(arn):
                node_id = f"{service}:{arn}"
            else:
                suffix = f"[{index_key}]" if index_key is not None else (
                    f"[{idx}]" if len(instances) > 1 else ""
                )
                node_id = f"terraform:{address}{suffix}"

            label = _label_for_resource(tf_type, tf_name, attrs)

            # Count and redact sensitive attrs before storing.
            # safe_attrs is used everywhere — never retain raw attrs after this point.
            original_count = len(attrs)
            safe_attrs = _redact_sensitive(attrs)
            self._redacted_count += original_count - len(safe_attrs)

            # Region from ARN or provider
            region = None
            node_arn = safe_attrs.get("arn", "")
            if node_arn and ":" in node_arn:
                arn_parts = node_arn.split(":")
                if len(arn_parts) >= 4 and arn_parts[3]:
                    region = arn_parts[3]

            self.store.add_node(
                node_id,
                label=label,
                service=service,
                type=node_type,
                region=region,
                source="terraform",
                tf_type=tf_type,
                tf_name=tf_name,
                tf_address=address,
                arn=safe_attrs.get("arn"),
                tags=safe_attrs.get("tags") or safe_attrs.get("tags_all"),
            )

            # Build lookup indices.  Edge inference uses safe_attrs only.
            if arn and _ARN_PATTERN.match(arn):
                self._arn_to_node[arn] = node_id
            self._address_to_node.setdefault(address, []).append(node_id)
            self._node_attrs[node_id] = safe_attrs  # redacted copy for edge inference
            self._node_service[node_id] = service
            self._node_tf_type[node_id] = tf_type
            self._node_label[node_id] = label

            # Secondary indices for O(1) lookups during edge inference.
            resource_id = safe_attrs.get("id", "")
            if service == "vpc" and resource_id:
                self._vpc_resource_id_to_node[resource_id] = node_id
            if service == "s3":
                bucket_name = safe_attrs.get("bucket") or safe_attrs.get("id", "")
                if bucket_name:
                    self._s3_bucket_name_to_node[bucket_name] = node_id
            if tf_type == "aws_ecs_cluster":
                cluster_arn = safe_attrs.get("arn", "")
                if cluster_arn:
                    self._ecs_cluster_arn_to_node[cluster_arn] = node_id
            if tf_type in ("aws_api_gateway_rest_api", "aws_apigatewayv2_api"):
                api_id = safe_attrs.get("id", "")
                if api_id:
                    self._apigw_id_to_node[api_id] = node_id
            if tf_type == "aws_iam_role":
                role_name = safe_attrs.get("name", "")
                if role_name:
                    self._iam_role_name_to_node[role_name] = node_id
            if tf_type == "aws_cloudwatch_event_rule":
                rule_name = safe_attrs.get("name", "")
                if rule_name:
                    self._eventbridge_rule_name_to_node[rule_name] = node_id

    # ------------------------------------------------------------------
    # Pass 2: Edge inference
    # ------------------------------------------------------------------

    def _resolve_arn(self, arn: str) -> Optional[str]:
        """Resolve an ARN to a known node ID, or None."""
        if not arn or not _ARN_PATTERN.match(arn):
            return None
        # Direct lookup
        node_id = self._arn_to_node.get(arn)
        if node_id:
            return node_id
        # Try service:arn format (how we store node IDs)
        svc = _service_from_arn(arn)
        if svc:
            candidate = f"{svc}:{arn}"
            if candidate in self._node_attrs:
                return candidate
        return None

    def _add_edge(self, source: str, target: str, **attrs: Any) -> None:
        """Add edge only if both endpoints exist in the graph."""
        if source and target and source != target:
            if source in self._node_attrs and target in self._node_attrs:
                self.store.add_edge(source, target, **attrs)

    def _infer_edges(self, resource: Dict[str, Any]) -> None:
        address = _tf_address(resource)
        node_ids = self._address_to_node.get(address, [])
        instances = resource.get("instances", [])
        tf_type = resource["type"]
        for idx, instance in enumerate(instances):
            attrs = instance.get("attributes") or {}
            if idx >= len(node_ids):
                break
            node_id = node_ids[idx]

            # Type-specific edge extractors
            if tf_type == "aws_lambda_function":
                self._edges_lambda(node_id, attrs)
            elif tf_type == "aws_lambda_event_source_mapping":
                self._edges_event_source_mapping(node_id, attrs)
            elif tf_type == "aws_lambda_permission":
                self._edges_lambda_permission(node_id, attrs)
            elif tf_type in ("aws_cloudwatch_event_target",):
                self._edges_eventbridge_target(node_id, attrs)
            elif tf_type in ("aws_api_gateway_integration", "aws_apigatewayv2_integration"):
                self._edges_apigw_integration(node_id, attrs)
            elif tf_type == "aws_sns_topic_subscription":
                self._edges_sns_subscription(node_id, attrs)
            elif tf_type == "aws_s3_bucket_notification":
                self._edges_s3_notification(node_id, attrs)
            elif tf_type == "aws_sfn_state_machine":
                self._edges_step_functions(node_id, attrs)
            elif tf_type == "aws_ecs_service":
                self._edges_ecs_service(node_id, attrs)
            elif tf_type == "aws_ecs_task_definition":
                self._edges_ecs_task_def(node_id, attrs)
            elif tf_type in ("aws_lb_listener", "aws_lb_listener_rule"):
                self._edges_lb_listener(node_id, attrs)
            elif tf_type == "aws_lb_target_group_attachment":
                self._edges_lb_tg_attachment(node_id, attrs)
            elif tf_type == "aws_cloudfront_distribution":
                self._edges_cloudfront(node_id, attrs)
            elif tf_type == "aws_iam_role_policy_attachment":
                self._edges_iam_attachment(node_id, attrs)

            # Generic ARN sweep for all types
            self._edges_generic_arn_sweep(node_id, attrs, tf_type)

    # -- Lambda --

    def _edges_lambda(self, node_id: str, attrs: Dict[str, Any]) -> None:
        # Execution role
        role_arn = attrs.get("role")
        target = self._resolve_arn(role_arn)
        if target:
            self._add_edge(node_id, target, relationship="assumes", via="execution_role")

        # Environment variable references
        env_block = _get_nested(attrs, "environment", "0", "variables")
        if not env_block and isinstance(attrs.get("environment"), list):
            env_list = attrs["environment"]
            if env_list and isinstance(env_list[0], dict):
                env_block = env_list[0].get("variables", {})
        if isinstance(env_block, dict):
            for env_key, env_val in env_block.items():
                if isinstance(env_val, str) and _ARN_PATTERN.match(env_val):
                    target = self._resolve_arn(env_val)
                    if target:
                        self._add_edge(
                            node_id, target,
                            relationship="references", via=f"env:{env_key}",
                        )
                # Naming convention inference — O(1) label lookup via _node_label index
                for suffix, (svc, _ntype) in _ENV_VAR_CONVENTIONS.items():
                    if env_key.upper().endswith(suffix) and isinstance(env_val, str) and env_val:
                        for nid, lbl in self._node_label.items():
                            if self._node_service.get(nid) == svc and lbl == env_val:
                                self._add_edge(
                                    node_id, nid,
                                    relationship="references",
                                    via=f"env_convention:{env_key}",
                                )
                                break

        # VPC config — O(1) lookup via _vpc_resource_id_to_node index
        vpc_config = attrs.get("vpc_config")
        if isinstance(vpc_config, list) and vpc_config:
            vc = vpc_config[0] if isinstance(vpc_config[0], dict) else {}
            for subnet_id in (vc.get("subnet_ids") or []):
                nid = self._vpc_resource_id_to_node.get(subnet_id)
                if nid:
                    self._add_edge(nid, node_id, relationship="contains")
            for sg_id in (vc.get("security_group_ids") or []):
                nid = self._vpc_resource_id_to_node.get(sg_id)
                if nid:
                    self._add_edge(nid, node_id, relationship="protects")

        # Dead letter config
        dlq_arn = _get_nested(attrs, "dead_letter_config", "0", "target_arn")
        if dlq_arn:
            target = self._resolve_arn(dlq_arn)
            if target:
                self._add_edge(node_id, target, relationship="dead_letter")

    # -- Event Source Mapping --

    def _edges_event_source_mapping(self, node_id: str, attrs: Dict[str, Any]) -> None:
        source_arn = attrs.get("event_source_arn")
        fn_arn = attrs.get("function_arn") or attrs.get("function_name")
        source = self._resolve_arn(source_arn)
        target = self._resolve_arn(fn_arn)
        if source and target:
            self._add_edge(source, target, relationship="triggers", via="event_source_mapping")

    # -- Lambda Permission --

    def _edges_lambda_permission(self, node_id: str, attrs: Dict[str, Any]) -> None:
        fn_name = attrs.get("function_name")
        source_arn = attrs.get("source_arn")
        fn_target = self._resolve_arn(fn_name)
        source_node = self._resolve_arn(source_arn)
        if source_node and fn_target:
            self._add_edge(source_node, fn_target, relationship="invokes", via="resource_policy")

    # -- EventBridge Target --

    def _edges_eventbridge_target(self, node_id: str, attrs: Dict[str, Any]) -> None:
        target_arn = attrs.get("arn")
        rule_ref = attrs.get("rule")
        # Resolve rule: try ARN index first, then name index — both O(1)
        rule_node = self._resolve_arn(rule_ref) if rule_ref else None
        if not rule_node and rule_ref:
            rule_node = self._eventbridge_rule_name_to_node.get(rule_ref)
        target_node = self._resolve_arn(target_arn)
        if rule_node and target_node:
            self._add_edge(rule_node, target_node, relationship="triggers", via="event_target")

    # -- API Gateway Integration --

    def _edges_apigw_integration(self, node_id: str, attrs: Dict[str, Any]) -> None:
        uri = attrs.get("uri") or attrs.get("integration_uri") or ""
        # Extract Lambda ARN from API GW integration URI
        lambda_match = re.search(
            r"arn:aws:lambda:[a-z0-9-]+:\d{12}:function:[a-zA-Z0-9_-]+", uri
        )
        if lambda_match:
            target = self._resolve_arn(lambda_match.group(0))
            if target:
                # Find parent API node via O(1) index
                rest_api_id = attrs.get("rest_api_id") or attrs.get("api_id")
                api_node = self._apigw_id_to_node.get(rest_api_id) if rest_api_id else None
                source = api_node or node_id
                self._add_edge(source, target, relationship="integrates", via="api_integration")

    # -- SNS Subscription --

    def _edges_sns_subscription(self, node_id: str, attrs: Dict[str, Any]) -> None:
        topic_arn = attrs.get("topic_arn")
        endpoint = attrs.get("endpoint")
        topic_node = self._resolve_arn(topic_arn)
        endpoint_node = self._resolve_arn(endpoint)
        if topic_node and endpoint_node:
            self._add_edge(
                topic_node, endpoint_node,
                relationship="delivers", via="subscription",
            )

    # -- S3 Notification --

    def _edges_s3_notification(self, node_id: str, attrs: Dict[str, Any]) -> None:
        bucket = attrs.get("bucket")
        # Find the S3 bucket node
        # O(1) lookup via _s3_bucket_name_to_node index
        bucket_node = self._s3_bucket_name_to_node.get(bucket) if bucket else None
        if not bucket_node:
            return
        for notif_key in ("lambda_function", "queue", "topic"):
            block = attrs.get(notif_key) or []
            if isinstance(block, list):
                for entry in block:
                    if not isinstance(entry, dict):
                        continue
                    arn_key = {
                        "lambda_function": "lambda_function_arn",
                        "queue": "queue_arn",
                        "topic": "topic_arn",
                    }[notif_key]
                    target = self._resolve_arn(entry.get(arn_key))
                    if target:
                        self._add_edge(
                            bucket_node, target,
                            relationship="notifies", via="s3_notification",
                        )

    # -- Step Functions --

    def _edges_step_functions(self, node_id: str, attrs: Dict[str, Any]) -> None:
        definition = attrs.get("definition")
        if not isinstance(definition, str):
            return
        try:
            defn = json.loads(definition)
        except (json.JSONDecodeError, TypeError):
            return
        # Extract all Resource ARNs from state machine definition
        arns = _extract_arns_from_value(defn)
        for arn in arns:
            target = self._resolve_arn(arn)
            if target:
                self._add_edge(
                    node_id, target,
                    relationship="invokes", via="state_machine_definition",
                )

        # Execution role
        role_arn = attrs.get("role_arn")
        role_target = self._resolve_arn(role_arn)
        if role_target:
            self._add_edge(node_id, role_target, relationship="assumes", via="execution_role")

    # -- ECS Service --

    def _edges_ecs_service(self, node_id: str, attrs: Dict[str, Any]) -> None:
        # Task definition
        td_arn = attrs.get("task_definition")
        target = self._resolve_arn(td_arn)
        if target:
            self._add_edge(node_id, target, relationship="runs", via="task_definition")

        # Cluster
        cluster_arn = attrs.get("cluster")
        cluster_node = self._resolve_arn(cluster_arn)
        if cluster_node:
            self._add_edge(cluster_node, node_id, relationship="contains")

        # Load balancer
        lb_block = attrs.get("load_balancer") or []
        if isinstance(lb_block, list):
            for entry in lb_block:
                if isinstance(entry, dict):
                    tg_arn = entry.get("target_group_arn")
                    tg_node = self._resolve_arn(tg_arn)
                    if tg_node:
                        self._add_edge(tg_node, node_id, relationship="routes_to")

    # -- ECS Task Definition --

    def _edges_ecs_task_def(self, node_id: str, attrs: Dict[str, Any]) -> None:
        # Execution role
        for role_key in ("execution_role_arn", "task_role_arn"):
            role_arn = attrs.get(role_key)
            target = self._resolve_arn(role_arn)
            if target:
                self._add_edge(node_id, target, relationship="assumes", via=role_key)

    # -- LB Listener --

    def _edges_lb_listener(self, node_id: str, attrs: Dict[str, Any]) -> None:
        lb_arn = attrs.get("load_balancer_arn")
        lb_node = self._resolve_arn(lb_arn)
        if lb_node:
            self._add_edge(lb_node, node_id, relationship="contains")

        # Default action → target group
        actions = attrs.get("default_action") or []
        if isinstance(actions, list):
            for action in actions:
                if isinstance(action, dict):
                    tg_arn = action.get("target_group_arn")
                    tg_node = self._resolve_arn(tg_arn)
                    if tg_node:
                        self._add_edge(node_id, tg_node, relationship="forwards_to")

    # -- LB Target Group Attachment --

    def _edges_lb_tg_attachment(self, node_id: str, attrs: Dict[str, Any]) -> None:
        tg_arn = attrs.get("target_group_arn")
        target_id = attrs.get("target_id")
        tg_node = self._resolve_arn(tg_arn)
        target_node = self._resolve_arn(target_id)
        if tg_node and target_node:
            self._add_edge(tg_node, target_node, relationship="routes_to")

    # -- CloudFront --

    def _edges_cloudfront(self, node_id: str, attrs: Dict[str, Any]) -> None:
        origins = attrs.get("origin") or []
        if isinstance(origins, list):
            for origin in origins:
                if not isinstance(origin, dict):
                    continue
                domain = origin.get("domain_name", "")
                # Try to match S3 bucket via O(1) index
                if ".s3." in domain or domain.endswith(".s3.amazonaws.com"):
                    bucket_name = domain.split(".")[0]
                    nid = self._s3_bucket_name_to_node.get(bucket_name)
                    if nid:
                        self._add_edge(node_id, nid, relationship="origin")
                # Try to match ALB
                origin_arn = origin.get("origin_id", "")
                if _ARN_PATTERN.match(origin_arn):
                    target = self._resolve_arn(origin_arn)
                    if target:
                        self._add_edge(node_id, target, relationship="origin")

        # WAF association
        waf_arn = attrs.get("web_acl_id")
        if waf_arn:
            target = self._resolve_arn(waf_arn)
            if target:
                self._add_edge(target, node_id, relationship="protects")

    # -- IAM Role Policy Attachment --

    def _edges_iam_attachment(self, node_id: str, attrs: Dict[str, Any]) -> None:
        role_name = attrs.get("role")
        policy_arn = attrs.get("policy_arn")
        # O(1) lookup via _iam_role_name_to_node index
        role_node = self._iam_role_name_to_node.get(role_name) if role_name else None
        policy_node = self._resolve_arn(policy_arn)
        if role_node and policy_node:
            self._add_edge(policy_node, role_node, relationship="attached_to")

    # -- Generic ARN sweep --

    _SKIP_GENERIC_ATTRS = frozenset({
        "arn", "id", "role", "role_arn", "execution_role_arn", "task_role_arn",
        "function_arn", "function_name", "event_source_arn", "topic_arn",
        "endpoint", "source_arn", "target_arn", "uri", "integration_uri",
        "load_balancer_arn", "target_group_arn", "policy_arn", "web_acl_id",
        "task_definition", "cluster",
        # Already handled by type-specific extractors — skip to avoid duplicate edges
        "dead_letter_config", "environment", "vpc_config", "definition",
    })

    def _edges_generic_arn_sweep(
        self, node_id: str, attrs: Dict[str, Any], tf_type: str,
    ) -> None:
        """Catch-all: walk all attribute values for ARNs not caught by specific extractors."""
        for key, value in attrs.items():
            if key in self._SKIP_GENERIC_ATTRS:
                continue
            if _is_sensitive_key(key):
                continue
            arns = _extract_arns_from_value(value)
            for arn in arns:
                target = self._resolve_arn(arn)
                if target and target != node_id:
                    self._add_edge(
                        node_id, target,
                        relationship="references", via=f"attr:{key}",
                    )


# ---------------------------------------------------------------------------
# Validation helpers (used by the API endpoint)
# ---------------------------------------------------------------------------


def validate_tfstate_content(raw_bytes: bytes, filename: str) -> Dict[str, Any]:
    """Parse and validate a .tfstate file. Returns the parsed dict or raises ValueError."""
    if len(raw_bytes) > MAX_BYTES_PER_FILE:
        raise ValueError(f"File too large: {filename} ({len(raw_bytes)} bytes, max {MAX_BYTES_PER_FILE})")

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"File is not valid UTF-8: {filename}")

    # Must start with '{' (JSON object)
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        raise ValueError(f"File does not appear to be JSON: {filename}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError(f"File contains invalid JSON: {filename}")

    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {filename}, got {type(data).__name__}")

    if "resources" not in data:
        raise ValueError(f"No 'resources' key found in {filename}. Is this a .tfstate file?")

    resources = data.get("resources")
    if not isinstance(resources, list):
        raise ValueError(f"'resources' in {filename} must be a list, got {type(resources).__name__}")

    if len(resources) > MAX_RESOURCES_PER_FILE:
        raise ValueError(
            f"File {filename} contains {len(resources)} resources, "
            f"which exceeds the maximum of {MAX_RESOURCES_PER_FILE}."
        )

    return data
