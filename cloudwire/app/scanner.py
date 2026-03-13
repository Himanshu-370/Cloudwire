from __future__ import annotations

import json
import logging
import re
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .graph_store import GraphStore
from .models import ScanMode, normalize_service_name as _normalize_service_name


def _safe_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


_ARN_PATTERN = re.compile(r"^arn:aws:[a-z0-9-]+:")

# Well-known Lambda environment variable suffixes that imply a resource reference.
# Mapping of suffix -> (service, node_type).
_ENV_VAR_CONVENTIONS: Dict[str, Tuple[str, str]] = {
    "_TABLE_NAME": ("dynamodb", "table"),
    "_TABLE": ("dynamodb", "table"),
    "_QUEUE_URL": ("sqs", "queue"),
    "_QUEUE_NAME": ("sqs", "queue"),
    "_BUCKET": ("s3", "bucket"),
    "_BUCKET_NAME": ("s3", "bucket"),
    "_STREAM_NAME": ("kinesis", "stream"),
    "_CLUSTER_NAME": ("ecs", "cluster"),
    "_CLUSTER": ("ecs", "cluster"),
    "_CACHE_ENDPOINT": ("elasticache", "cluster"),
}


@dataclass
class ScanExecutionOptions:
    mode: ScanMode = "quick"
    include_iam_inference: bool = False
    include_resource_describes: bool = False
    max_service_workers: int = 5
    apigw_integration_workers: int = 16
    eventbridge_target_workers: int = 8
    dynamodb_describe_workers: int = 16
    sqs_attribute_workers: int = 16
    iam_workers: int = 8
    ecs_describe_workers: int = 4


class ScanCancelledError(Exception):
    pass


# Service name -> resource type filter(s) for the tagging API.
# Used by _fetch_and_apply_tags to batch-fetch tags for scanned resources.
_SERVICE_TO_RESOURCE_TYPE_FILTER: Dict[str, List[str]] = {
    "apigateway": ["apigateway"],
    "lambda": ["lambda:function"],
    "sqs": ["sqs"],
    "eventbridge": ["events"],
    "dynamodb": ["dynamodb:table"],
    "ec2": ["ec2:instance"],
    "ecs": ["ecs:cluster", "ecs:service", "ecs:task-definition"],
    "s3": ["s3"],
    "rds": ["rds:db", "rds:cluster"],
    "stepfunctions": ["states:stateMachine"],
    "sns": ["sns"],
    "kinesis": ["kinesis:stream"],
    "cognito": ["cognito-idp:userpool"],
    "cloudfront": ["cloudfront:distribution"],
    "elasticache": ["elasticache:cluster"],
    "glue": ["glue:job", "glue:crawler"],
    "appsync": ["appsync"],
    "route53": ["route53:hostedzone"],
    "redshift": ["redshift:cluster"],
    "iam": [],  # IAM tags are global, handled differently
    "vpc": ["ec2:vpc", "ec2:subnet", "ec2:security-group", "ec2:internet-gateway", "ec2:natgateway", "ec2:route-table"],
}


class AWSGraphScanner:
    # IAM action prefix -> normalized service name for policy dependency inference
    _IAM_PREFIX_TO_SERVICE: Dict[str, str] = {
        "dynamodb": "dynamodb",
        "sqs": "sqs",
        "events": "eventbridge",
        "lambda": "lambda",
        "s3": "s3",
        "sns": "sns",
        "kinesis": "kinesis",
        "states": "stepfunctions",
        "rds-data": "rds",
        "rds": "rds",
        "secretsmanager": "secretsmanager",
        "kms": "kms",
        "ecs": "ecs",
        "execute-api": "apigateway",
        "elasticache": "elasticache",
        "redshift-data": "redshift",
        "glue": "glue",
        "cognito-idp": "cognito",
        "appsync": "appsync",
    }

    def __init__(self, store: GraphStore, *, options: ScanExecutionOptions) -> None:
        self.store = store
        self.options = options
        self._region: str = "unknown"
        self._account_id: str = "unknown"
        self.service_scanners: Dict[str, Callable[[boto3.session.Session], None]] = {
            "apigateway":    self._scan_apigateway,
            "lambda":        self._scan_lambda,
            "sqs":           self._scan_sqs,
            "eventbridge":   self._scan_eventbridge,
            "dynamodb":      self._scan_dynamodb,
            "ec2":           self._scan_ec2,
            "ecs":           self._scan_ecs,
            "s3":            self._scan_s3,
            "rds":           self._scan_rds,
            "stepfunctions": self._scan_stepfunctions,
            "sns":           self._scan_sns,
            "kinesis":       self._scan_kinesis,
            "iam":           self._scan_iam,
            "cognito":       self._scan_cognito,
            "cloudfront":    self._scan_cloudfront,
            "elasticache":   self._scan_elasticache,
            "glue":          self._scan_glue,
            "appsync":       self._scan_appsync,
            "route53":       self._scan_route53,
            "redshift":      self._scan_redshift,
            "vpc":           self._scan_vpc,
        }
        self._iam_role_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._iam_cache_lock = Lock()
        self._node_attr_index: Dict[tuple, str] = {}  # (service, attr, value) -> node_id
        self._metrics_lock = Lock()
        self._api_call_counts: Dict[str, int] = {}
        self._service_durations_ms: Dict[str, int] = {}
        self._should_cancel: Optional[Callable[[], bool]] = None
        self._client_config = Config(
            retries={"mode": "adaptive", "max_attempts": 10},
            max_pool_connections=64,
            connect_timeout=3,
            read_timeout=20,
        )

    def scan(
        self,
        *,
        region: str,
        services: List[str],
        account_id: str = "unknown",
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        normalized_services = list(dict.fromkeys(_normalize_service_name(service) for service in services))
        self._region = region
        self._account_id = account_id
        self.store.reset(region=region, services=normalized_services)
        self._iam_role_cache = {}
        self._api_call_counts = {}
        self._service_durations_ms = {}
        self._should_cancel = should_cancel

        if not self.options.include_iam_inference:
            self.store.add_warning("IAM policy dependency inference skipped for faster quick scan mode.")
        if not self.options.include_resource_describes:
            self.store.add_warning("Resource describe enrichment skipped for faster quick scan mode.")

        session = boto3.session.Session(region_name=region)
        has_vpc = "vpc" in normalized_services
        total_services = len(normalized_services)
        completed = 0
        started_at = perf_counter()

        # Phase 1: run all non-VPC scanners in parallel
        phase1_count = max(1, len(normalized_services) - (1 if has_vpc else 0))
        workers = max(1, min(self.options.max_service_workers, phase1_count))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_service: Dict[Any, str] = {}
            for service in normalized_services:
                if service == "vpc":
                    continue  # handled in Phase 2
                if self._is_cancelled():
                    break
                if progress_callback:
                    progress_callback("start", service, completed, total_services)
                future_to_service[pool.submit(self._scan_service, session, service)] = service

            def on_service_result(future: Future[Any], service: str) -> None:
                nonlocal completed
                try:
                    duration_ms = future.result()
                    with self._metrics_lock:
                        self._service_durations_ms[service] = duration_ms
                except ScanCancelledError:
                    pass
                except Exception as exc:
                    logger.exception("Unhandled error draining future for service %s", service)
                    self.store.add_warning(f"{service} scan failed: {type(exc).__name__} - {exc}")
                finally:
                    completed += 1
                    if progress_callback:
                        progress_callback("finish", service, completed, total_services)

            self._drain_futures(future_to_service, on_service_result)

        # Phase 2: scoped VPC scan — only fetch VPCs referenced by Phase 1 scanners
        if has_vpc and not self._is_cancelled():
            if progress_callback:
                progress_callback("start", "vpc", completed, total_services)
            vpc_ids = self._collect_referenced_vpc_ids()
            logger.info("Phase 2 VPC scan: %d referenced VPC(s)%s",
                        len(vpc_ids),
                        f" {list(vpc_ids)}" if 0 < len(vpc_ids) <= 10 else "")
            vpc_start = perf_counter()
            try:
                self._scan_vpc(session, vpc_ids=vpc_ids if vpc_ids else None)
                self._fetch_and_apply_tags(session, "vpc")
            except ScanCancelledError:
                pass
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code", "")
                if error_code in ("AccessDenied", "AccessDeniedException", "UnauthorizedAccess"):
                    logger.warning("Permission denied scanning vpc: %s", error_code)
                    self.store.add_warning("[permission] vpc: access denied — check IAM permissions for this service")
                else:
                    logger.warning("AWS API error scanning vpc: %s", exc)
                    self.store.add_warning(f"vpc scan failed: {type(exc).__name__} - {exc}")
            except (BotoCoreError, Exception) as exc:
                logger.exception("Error in VPC phase-2 scan")
                self.store.add_warning(f"vpc scan failed: {type(exc).__name__} - {exc}")
            with self._metrics_lock:
                self._service_durations_ms["vpc"] = int((perf_counter() - vpc_start) * 1000)
            completed += 1
            if progress_callback:
                progress_callback("finish", "vpc", completed, total_services)

        # Post-scan: compute internet exposure if VPC topology was scanned
        self._compute_network_exposure()

        duration_ms = int((perf_counter() - started_at) * 1000)
        self.store.update_metadata(
            account_id=account_id,
            scan_mode=self.options.mode,
            include_iam_inference=self.options.include_iam_inference,
            include_resource_describes=self.options.include_resource_describes,
            total_scan_ms=duration_ms,
            service_durations_ms=self._service_durations_ms,
            aws_api_call_counts=self._api_call_counts,
        )
        return self.store.get_graph_payload()

    def _scan_service(self, session: boto3.session.Session, service: str) -> int:
        start = perf_counter()
        if self._is_cancelled():
            return 0
        scanner = self.service_scanners.get(service)
        try:
            if scanner:
                scanner(session)
                # Batch-fetch tags for dedicated scanners (generic already fetches tags)
                self._fetch_and_apply_tags(session, service)
            else:
                self._scan_generic_service(session, service)
        except ScanCancelledError:
            return int((perf_counter() - start) * 1000)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("AccessDenied", "AccessDeniedException", "UnauthorizedAccess"):
                logger.warning("Permission denied scanning %s: %s", service, error_code)
                self.store.add_warning(f"[permission] {service}: access denied — check IAM permissions for this service")
            else:
                logger.warning("AWS API error scanning %s: %s", service, exc)
                self.store.add_warning(f"{service} scan failed: {type(exc).__name__} - {exc}")
        except BotoCoreError as exc:
            logger.warning("AWS API error scanning %s: %s", service, exc)
            self.store.add_warning(f"{service} scan failed: {type(exc).__name__} - {exc}")
        except Exception as exc:
            logger.exception("Unexpected error scanning service %s", service)
            self.store.add_warning(f"{service} scan failed: {type(exc).__name__} - {exc}")
        return int((perf_counter() - start) * 1000)

    def _client(self, session: boto3.session.Session, service_name: str) -> Any:
        return session.client(service_name, config=self._client_config)

    def _increment_api_call(self, service: str, operation: str) -> None:
        self._ensure_not_cancelled()
        key = f"{service}.{operation}"
        with self._metrics_lock:
            self._api_call_counts[key] = self._api_call_counts.get(key, 0) + 1

    def _is_cancelled(self) -> bool:
        if not self._should_cancel:
            return False
        return bool(self._should_cancel())

    def _ensure_not_cancelled(self) -> None:
        if self._is_cancelled():
            raise ScanCancelledError()

    def _node(self, node_id: str, **attrs: Any) -> None:
        self.store.add_node(node_id, region=self._region, **attrs)
        service = attrs.get("service")
        if service:
            for attr_name in ("domain", "label"):
                val = attrs.get(attr_name)
                if val:
                    self._node_attr_index[(service, attr_name, val)] = node_id

    @staticmethod
    def _parse_sg_rules(permissions: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Parse AWS SG IpPermissions into a flat list of rule dicts."""
        rules: List[Dict[str, str]] = []
        for perm in permissions:
            protocol = perm.get("IpProtocol", "-1")
            from_port = perm.get("FromPort", 0)
            to_port = perm.get("ToPort", 0)
            if protocol == "-1":
                port_range = "all"
            elif from_port == to_port:
                port_range = f"{from_port}/{protocol}"
            else:
                port_range = f"{from_port}-{to_port}/{protocol}"
            # CIDR-based rules
            for ip_range in perm.get("IpRanges", []):
                rules.append({"protocol": protocol, "port_range": port_range,
                              "source": ip_range.get("CidrIp", ""), "source_type": "cidr"})
            for ip_range in perm.get("Ipv6Ranges", []):
                rules.append({"protocol": protocol, "port_range": port_range,
                              "source": ip_range.get("CidrIpv6", ""), "source_type": "cidr"})
            # SG-to-SG rules
            for pair in perm.get("UserIdGroupPairs", []):
                rules.append({"protocol": protocol, "port_range": port_range,
                              "source": pair.get("GroupId", ""), "source_type": "sg"})
        return rules

    def _drain_futures(
        self,
        future_map: Dict[Future[Any], Any],
        on_result: Callable[[Future[Any], Any], None],
    ) -> None:
        pending = set(future_map)
        cancel_attempted = False
        while pending:
            if self._is_cancelled() and not cancel_attempted:
                cancel_attempted = True
                for future in list(pending):
                    if future.cancel():
                        pending.remove(future)

            done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for future in done:
                on_result(future, future_map[future])

        self._ensure_not_cancelled()

    def _service_from_arn(self, arn: str) -> str:
        parts = arn.split(":")
        return parts[2] if len(parts) > 2 else "unknown"

    def _make_node_id(self, service: str, resource: str) -> str:
        return f"{service}:{resource}"

    def _find_node_by_attr(self, service: str, attr: str, value: str) -> Optional[str]:
        """Find an existing node of a given service where attr == value. Returns node_id or None."""
        return self._node_attr_index.get((service, attr, value))

    def _add_arn_node(self, arn: str, *, label: Optional[str] = None, node_type: str = "resource") -> str:
        self._ensure_not_cancelled()
        service = self._service_from_arn(arn)
        node_id = self._make_node_id(service, arn)
        self._node(
            node_id,
            label=label or arn.split(":")[-1],
            arn=arn,
            service=service,
            type=node_type,
        )
        return node_id

    def _fetch_and_apply_tags(self, session: boto3.session.Session, service_name: str) -> None:
        """Batch-fetch tags for all resources of a service and apply them to graph nodes.

        Builds multiple lookup indices to match tagging API ARNs to graph nodes,
        since some scanners store non-ARN values (table names, queue URLs) as the
        node's 'arn' attribute.
        """
        resource_types = _SERVICE_TO_RESOURCE_TYPE_FILTER.get(service_name, [])
        if not resource_types:
            return
        try:
            client = self._client(session, "resourcegroupstaggingapi")

            # Build multiple lookup indices for matching
            arn_to_node: Dict[str, str] = {}      # exact 'arn' attr match
            name_to_node: Dict[str, str] = {}     # resource name match (last segment of ARN)
            node_id_to_node: Dict[str, str] = {}  # node_id contains the ARN

            with self.store._lock:
                for node_id, attrs in self.store.graph.nodes(data=True):
                    if attrs.get("service") != service_name:
                        continue
                    node_arn = attrs.get("arn")
                    if node_arn:
                        arn_to_node[node_arn] = node_id
                    label = attrs.get("label", "")
                    if label:
                        name_to_node[label] = node_id
                    node_id_to_node[node_id] = node_id

            if not arn_to_node and not name_to_node:
                return

            paginator = client.get_paginator("get_resources")
            for rt in resource_types:
                if self._is_cancelled():
                    return
                try:
                    for page in paginator.paginate(ResourcesPerPage=100, ResourceTypeFilters=[rt]):
                        self._increment_api_call("resourcegroupstaggingapi", "get_resources")
                        for entry in page.get("ResourceTagMappingList", []):
                            arn = entry.get("ResourceARN")
                            if not arn:
                                continue
                            # Try matching: exact arn, then node_id containing arn,
                            # then by resource name (last ARN segment)
                            matched_node = arn_to_node.get(arn)
                            if not matched_node:
                                # Node ID format is 'service:arn', check if it exists
                                candidate_id = f"{service_name}:{arn}"
                                matched_node = node_id_to_node.get(candidate_id)
                            if not matched_node:
                                # Match by resource name (last part of ARN after / or :)
                                resource_name = arn.rsplit("/", 1)[-1] if "/" in arn else arn.rsplit(":", 1)[-1]
                                matched_node = name_to_node.get(resource_name)
                            if matched_node:
                                tags = {item.get("Key"): item.get("Value") for item in entry.get("Tags", [])}
                                self._node(matched_node, tags=tags, real_arn=arn)
                except (ClientError, BotoCoreError) as exc:
                    logger.debug("Tag fetch failed for resource type %s: %s", rt, exc)
        except (ClientError, BotoCoreError) as exc:
            logger.debug("Tag fetch failed for service %s: %s", service_name, exc)

    def _collect_referenced_vpc_ids(self) -> Set[str]:
        """Collect VPC IDs that were referenced by non-VPC scanners as stub nodes."""
        vpc_ids: Set[str] = set()
        with self.store._lock:
            for node_id, attrs in self.store.graph.nodes(data=True):
                if attrs.get("service") == "vpc" and attrs.get("type") == "vpc":
                    # node_id format: "vpc:vpc/{vpc_id}"
                    parts = node_id.split("vpc/", 1)
                    if len(parts) == 2:
                        vpc_ids.add(parts[1])
        return vpc_ids

    def _compute_network_exposure(self) -> None:
        """Compute exposed_internet flag by tracing IGW → route table → subnet → SG → resource paths.

        Runs after all scanners complete — no concurrent graph mutations expected.
        Snapshots the graph under the lock, traverses the snapshot, then applies results.
        """
        # Snapshot graph data under the lock to avoid holding it during traversal
        with self.store._lock:
            graph = self.store.graph.copy()

        # Find all IGW nodes
        igw_nodes = [n for n, d in graph.nodes(data=True) if d.get("type") == "internet_gateway"]
        if not igw_nodes:
            return

        # Build subnet → resources that are "contained" in that subnet
        subnet_resources: Dict[str, List[str]] = {}
        # Build resource → protecting SGs
        resource_sgs: Dict[str, List[str]] = {}

        for src, tgt, attrs in graph.edges(data=True):
            rel = attrs.get("relationship", "")
            if rel == "contains":
                src_type = graph.nodes[src].get("type", "")
                if src_type == "subnet":
                    subnet_resources.setdefault(src, []).append(tgt)
            elif rel == "protects":
                resource_sgs.setdefault(tgt, []).append(src)

        # Build IGW → Internet node mapping (for path node IDs)
        igw_to_internet: Dict[str, str] = {}
        for src, tgt, attrs in graph.edges(data=True):
            if attrs.get("relationship") == "gateway":
                igw_to_internet[tgt] = src  # internet_node → igw_node, so tgt=igw, src=internet

        # For each IGW, find route tables it feeds into, then subnets those RTBs route to
        # Store both path string and node IDs for frontend highlighting
        internet_reachable_subnets: Dict[str, tuple] = {}  # subnet_node -> (path_string, [node_ids])

        for igw_node in igw_nodes:
            igw_label = graph.nodes[igw_node].get("label", igw_node)
            internet_node = igw_to_internet.get(igw_node)
            # IGW → routes_via → RTB (direct edge from _scan_vpc)
            for _, rtb_node, e_attrs in graph.out_edges(igw_node, data=True):
                if e_attrs.get("relationship") != "routes_via":
                    continue
                rtb_label = graph.nodes[rtb_node].get("label", rtb_node)
                # RTB → routes → subnet
                for _, subnet_node, r_attrs in graph.out_edges(rtb_node, data=True):
                    if r_attrs.get("relationship") == "routes":
                        path_str = f"{igw_label} → {rtb_label}"
                        path_nodes = [n for n in [internet_node, igw_node, rtb_node, subnet_node] if n]
                        internet_reachable_subnets[subnet_node] = (path_str, path_nodes)

        # Mark resources in internet-reachable subnets with open SGs
        exposure_updates: List[tuple] = []
        for subnet_node, (path_prefix, path_nodes_prefix) in internet_reachable_subnets.items():
            subnet_label = graph.nodes[subnet_node].get("label", subnet_node)
            for resource_node in subnet_resources.get(subnet_node, []):
                # Skip VPC infra nodes themselves
                if graph.nodes[resource_node].get("service") == "vpc":
                    continue
                # Check if any protecting SG allows all ingress
                for sg_node in resource_sgs.get(resource_node, []):
                    if graph.nodes[sg_node].get("has_open_ingress"):
                        open_sg = graph.nodes[sg_node].get("label", sg_node)
                        path = f"{path_prefix} → {subnet_label} → {open_sg}"
                        path_node_ids = path_nodes_prefix + [sg_node, resource_node]
                        exposure_updates.append((resource_node, path, path_node_ids))
                        break

        # Apply results under the lock
        if exposure_updates:
            with self.store._lock:
                for resource_node, path, path_node_ids in exposure_updates:
                    if self.store.graph.has_node(resource_node):
                        self.store.graph.nodes[resource_node]["exposed_internet"] = True
                        self.store.graph.nodes[resource_node]["internet_path"] = path
                        self.store.graph.nodes[resource_node]["internet_path_nodes"] = path_node_ids

    def _parse_lambda_arn(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        if ":function:" in value:
            clean = value.split("/invocations")[0]
            idx = clean.find("arn:aws:lambda:")
            if idx >= 0:
                return clean[idx:]
        return None

    def _base_lambda_arn(self, function_arn: str) -> str:
        if ":function:" not in function_arn:
            return function_arn
        prefix, suffix = function_arn.split(":function:", 1)
        function_name = suffix.split(":", 1)[0]
        return f"{prefix}:function:{function_name}"

    def _scan_apigateway(self, session: boto3.session.Session) -> None:
        self._scan_apigateway_v2(session)
        self._scan_apigateway_rest(session)

    def _scan_apigateway_v2(self, session: boto3.session.Session) -> None:
        client = self._client(session, "apigatewayv2")
        apis: List[tuple[str, str]] = []  # (api_id, node_id)
        next_token: Optional[str] = None

        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("apigateway", "get_apis")
            page = client.get_apis(**kwargs)
            for api in page.get("Items", []):
                self._ensure_not_cancelled()
                api_id = api["ApiId"]
                api_name = api.get("Name") or api_id
                node_id = self._make_node_id("apigateway", api_id)
                self._node(
                    node_id,
                    label=api_name,
                    service="apigateway",
                    type="api",
                    api_protocol=api.get("ProtocolType"),
                    api_endpoint=api.get("ApiEndpoint"),
                )
                apis.append((api_id, node_id))
            next_token = page.get("NextToken")
            if not next_token:
                break

        if not apis:
            return

        workers = max(1, min(self.options.apigw_integration_workers, len(apis)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._fetch_apigwv2_integrations, client, api_id): api_node
                for api_id, api_node in apis
            }
            self._drain_futures(futures, self._apply_apigwv2_integrations)

    def _fetch_apigwv2_integrations(self, client: Any, api_id: str) -> List[Dict[str, Any]]:
        integrations: List[Dict[str, Any]] = []
        next_token: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {"ApiId": api_id}
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("apigateway", "get_integrations")
            page = client.get_integrations(**kwargs)
            integrations.extend(page.get("Items", []))
            next_token = page.get("NextToken")
            if not next_token:
                break
        return integrations

    def _resolve_apigw_integration_target(self, integration: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        """Resolve an API Gateway integration to (target_node_id, relationship) or None."""
        uri = integration.get("IntegrationUri") or integration.get("uri") or ""
        subtype = integration.get("IntegrationSubtype") or ""

        # Lambda integrations (most common)
        lambda_arn = self._parse_lambda_arn(uri)
        if lambda_arn:
            return self._add_arn_node(lambda_arn, node_type="lambda"), "invokes"

        # Step Functions
        if "StepFunctions" in subtype or "states:::execution" in subtype or ":states:" in uri:
            arn = uri if _ARN_PATTERN.match(uri) else None
            if arn:
                return self._add_arn_node(arn), "invokes"

        # SQS
        if "SQS" in subtype or ":sqs:" in uri:
            arn = uri if _ARN_PATTERN.match(uri) else None
            if arn:
                return self._add_arn_node(arn), "sends_to"

        # SNS
        if "SNS" in subtype or ":sns:" in uri:
            arn = uri if _ARN_PATTERN.match(uri) else None
            if arn:
                return self._add_arn_node(arn), "publishes_to"

        # Kinesis
        if "Kinesis" in subtype or ":kinesis:" in uri:
            arn = uri if _ARN_PATTERN.match(uri) else None
            if arn:
                return self._add_arn_node(arn), "sends_to"

        # EventBridge
        if "EventBridge" in subtype or ":events:" in uri:
            arn = uri if _ARN_PATTERN.match(uri) else None
            if arn:
                return self._add_arn_node(arn), "sends_to"

        # Generic ARN fallback
        if _ARN_PATTERN.match(uri):
            return self._add_arn_node(uri), "integrates_with"

        return None

    def _apply_apigwv2_integrations(self, future: Future[Any], api_node: str) -> None:
        try:
            integrations = future.result()
        except Exception as exc:
            logger.debug("Failed to fetch API Gateway v2 integrations: %s", exc)
            return
        self._ensure_not_cancelled()
        for integration in integrations:
            self._ensure_not_cancelled()
            try:
                result = self._resolve_apigw_integration_target(integration)
                if not result:
                    continue
                target_node, relationship = result
                self.store.add_edge(
                    api_node, target_node,
                    relationship=relationship, via="apigatewayv2_integration",
                )
            except Exception as exc:
                logger.debug("Failed to resolve API Gateway v2 integration target: %s", exc)

    def _scan_apigateway_rest(self, session: boto3.session.Session) -> None:
        client = self._client(session, "apigateway")
        position: Optional[str] = None

        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {"limit": 500}
            if position:
                kwargs["position"] = position
            self._increment_api_call("apigateway", "get_rest_apis")
            page = client.get_rest_apis(**kwargs)
            for api in page.get("items", []):
                self._ensure_not_cancelled()
                rest_api_id = api["id"]
                api_node = self._make_node_id("apigateway", rest_api_id)
                self._node(
                    api_node,
                    label=api.get("name") or rest_api_id,
                    service="apigateway",
                    type="api",
                    endpoint_configuration=api.get("endpointConfiguration", {}),
                )

                tasks: List[tuple[str, str, str, str]] = []
                res_position: Optional[str] = None
                while True:
                    self._ensure_not_cancelled()
                    res_kwargs: Dict[str, Any] = {"restApiId": rest_api_id, "limit": 500}
                    if res_position:
                        res_kwargs["position"] = res_position
                    self._increment_api_call("apigateway", "get_resources")
                    resources_page = client.get_resources(**res_kwargs)
                    for resource in resources_page.get("items", []):
                        self._ensure_not_cancelled()
                        methods = resource.get("resourceMethods", {})
                        for http_method in methods.keys():
                            tasks.append((rest_api_id, resource["id"], http_method, api_node))
                    res_position = resources_page.get("position")
                    if not res_position:
                        break

                if tasks:
                    workers = max(1, min(self.options.apigw_integration_workers, len(tasks)))
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = {
                            pool.submit(
                                self._fetch_apigw_rest_integration,
                                client,
                                rest_api_id,
                                resource_id,
                                http_method,
                            ): api_node
                            for rest_api_id, resource_id, http_method, api_node in tasks
                        }
                        self._drain_futures(futures, self._apply_apigateway_rest_integration)

                # Phase 3, Item 10: Cognito authorizer edges
                if self.options.include_resource_describes:
                    self._scan_rest_api_authorizers(client, rest_api_id, api_node)

            position = page.get("position")
            if not position:
                break

    def _fetch_apigw_rest_integration(
        self,
        client: Any,
        rest_api_id: str,
        resource_id: str,
        http_method: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            self._increment_api_call("apigateway", "get_integration")
            return client.get_integration(
                restApiId=rest_api_id,
                resourceId=resource_id,
                httpMethod=http_method,
            )
        except ClientError as exc:
            logger.debug("Skipping API Gateway integration %s/%s/%s: %s", rest_api_id, resource_id, http_method, exc)
            return None

    def _apply_apigateway_rest_integration(self, future: Future[Any], api_node: str) -> None:
        try:
            integration = future.result()
        except Exception as exc:
            logger.debug("Failed to fetch REST API integration: %s", exc)
            return
        if not integration:
            return
        self._ensure_not_cancelled()
        try:
            result = self._resolve_apigw_integration_target(integration)
            if not result:
                return
            target_node, relationship = result
            self.store.add_edge(
                api_node, target_node,
                relationship=relationship, via="apigateway_rest_integration",
            )
        except Exception as exc:
            logger.debug("Failed to resolve REST API integration target: %s", exc)

    def _scan_rest_api_authorizers(self, client: Any, rest_api_id: str, api_node: str) -> None:
        """Discover Cognito user pool authorizers on a REST API (Phase 3, Item 10)."""
        try:
            self._ensure_not_cancelled()
            self._increment_api_call("apigateway", "get_authorizers")
            response = client.get_authorizers(restApiId=rest_api_id)
            for authorizer in response.get("items", []):
                auth_type = authorizer.get("type", "")
                if auth_type != "COGNITO_USER_POOLS":
                    continue
                for provider_arn in authorizer.get("providerARNs", []):
                    if not isinstance(provider_arn, str) or not _ARN_PATTERN.match(provider_arn):
                        continue
                    cognito_node = self._add_arn_node(provider_arn, node_type="user_pool")
                    self._node(cognito_node, service="cognito")
                    self.store.add_edge(
                        cognito_node, api_node,
                        relationship="authorizes", via="cognito_authorizer",
                    )
        except (ClientError, BotoCoreError) as exc:
            logger.debug("REST API authorizer scan skipped for %s: %s", rest_api_id, exc)
        except Exception as exc:
            logger.debug("Unexpected error scanning authorizers for REST API %s: %s", rest_api_id, exc)

    def _scan_lambda(self, session: boto3.session.Session) -> None:
        client = self._client(session, "lambda")
        paginator = client.get_paginator("list_functions")
        functions: List[Dict[str, Any]] = []
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("lambda", "list_functions")
            functions.extend(page.get("Functions", []))

        function_node_ids: Dict[str, str] = {}
        role_to_function_nodes: Dict[str, List[str]] = {}

        for fn in functions:
            self._ensure_not_cancelled()
            arn = fn["FunctionArn"]
            node_id = self._add_arn_node(arn, label=fn.get("FunctionName"), node_type="lambda")
            self._node(
                node_id,
                runtime=fn.get("Runtime"),
                handler=fn.get("Handler"),
                role=fn.get("Role"),
                memory_size=fn.get("MemorySize"),
                timeout=fn.get("Timeout"),
                last_modified=fn.get("LastModified"),
                state=fn.get("State"),
            )
            function_node_ids[arn] = node_id
            function_node_ids[self._base_lambda_arn(arn)] = node_id

            role_arn = fn.get("Role")
            if role_arn:
                role_name = role_arn.split("/")[-1]
                role_to_function_nodes.setdefault(role_name, []).append(node_id)
                # Phase 2, Item 4: IAM Role → Lambda edge
                if _ARN_PATTERN.match(role_arn):
                    role_node = self._add_arn_node(role_arn, label=role_name, node_type="role")
                    self._node(role_node, service="iam")
                    self.store.add_edge(role_node, node_id, relationship="assumed_by", via="lambda_execution_role")

            # Lambda VPC topology edges
            vpc_config = fn.get("VpcConfig", {})
            fn_vpc_id = vpc_config.get("VpcId")
            if fn_vpc_id:
                fn_vpc_node = self._make_node_id("vpc", f"vpc/{fn_vpc_id}")
                self._node(fn_vpc_node, label=fn_vpc_id, service="vpc", type="vpc")
                for fn_subnet_id in vpc_config.get("SubnetIds", []):
                    fn_subnet_node = self._make_node_id("vpc", f"subnet/{fn_subnet_id}")
                    self._node(fn_subnet_node, label=fn_subnet_id, service="vpc", type="subnet")
                    self.store.add_edge(fn_subnet_node, node_id, relationship="contains", via="lambda_vpc_placement")
                for fn_sg_id in vpc_config.get("SecurityGroupIds", []):
                    fn_sg_node = self._make_node_id("vpc", f"sg/{fn_sg_id}")
                    self._node(fn_sg_node, label=fn_sg_id, service="vpc", type="security_group")
                    self.store.add_edge(fn_sg_node, node_id, relationship="protects", via="lambda_security_group")

            # Phase 1, Item 1: Lambda env var edges
            self._extract_lambda_env_edges(fn, node_id)

        self._scan_lambda_event_sources_global(client, function_node_ids)
        if self.options.include_iam_inference:
            self._scan_lambda_iam_dependencies_parallel(session, role_to_function_nodes)

    def _extract_lambda_env_edges(self, fn: Dict[str, Any], function_node_id: str) -> None:
        """Extract edges from Lambda environment variables to referenced resources.

        Recognises explicit ARNs and well-known naming conventions (e.g. *_TABLE_NAME).
        Environment variable *values* are never logged to avoid leaking secrets.
        """
        env_vars = fn.get("Environment", {}).get("Variables", {})
        if not env_vars or not isinstance(env_vars, dict):
            return

        seen_targets: Set[str] = set()
        for key, value in env_vars.items():
            try:
                if not isinstance(value, str) or not value.strip():
                    continue
                value = value.strip()

                # 1. Explicit ARN reference
                if _ARN_PATTERN.match(value):
                    target = self._add_arn_node(value)
                    if target not in seen_targets:
                        seen_targets.add(target)
                        self.store.add_edge(
                            function_node_id, target,
                            relationship="references", via="lambda_env_var",
                        )
                    continue

                # 2. Naming convention fallback
                upper_key = key.upper()
                for suffix, (service, node_type) in _ENV_VAR_CONVENTIONS.items():
                    if not upper_key.endswith(suffix):
                        continue
                    # Reject values that look like config flags rather than resource names
                    if len(value) < 2 or len(value) > 256:
                        break
                    if service == "s3":
                        node_id = self._make_node_id("s3", value)
                        self._node(node_id, label=value, service="s3", type="bucket",
                                   arn=f"arn:aws:s3:::{value}")
                    else:
                        node_id = self._make_node_id(service, value)
                        self._node(node_id, label=value, service=service, type=node_type)
                    if node_id not in seen_targets:
                        seen_targets.add(node_id)
                        self.store.add_edge(
                            function_node_id, node_id,
                            relationship="references", via="lambda_env_var_convention",
                        )
                    break  # match at most one convention per variable
            except Exception:
                # Never let a single env var parsing error abort the scan.
                # Intentionally do not log the value (may contain secrets).
                logger.debug("Lambda env var edge extraction failed for key %s", key)

    def _scan_lambda_event_sources_global(self, client: Any, function_node_ids: Dict[str, str]) -> None:
        marker: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if marker:
                kwargs["Marker"] = marker
            self._increment_api_call("lambda", "list_event_source_mappings")
            page = client.list_event_source_mappings(**kwargs)
            for mapping in page.get("EventSourceMappings", []):
                self._ensure_not_cancelled()
                event_source_arn = mapping.get("EventSourceArn")
                function_arn = mapping.get("FunctionArn")
                if not event_source_arn or not function_arn:
                    continue

                function_node_id = function_node_ids.get(function_arn) or function_node_ids.get(
                    self._base_lambda_arn(function_arn)
                )
                if not function_node_id:
                    continue

                source_node = self._add_arn_node(event_source_arn)
                self.store.add_edge(
                    source_node,
                    function_node_id,
                    relationship="triggers",
                    via="lambda_event_source_mapping",
                    state=mapping.get("State"),
                )

            marker = page.get("NextMarker")
            if not marker:
                break

    def _scan_lambda_iam_dependencies_parallel(
        self,
        session: boto3.session.Session,
        role_to_function_nodes: Dict[str, List[str]],
    ) -> None:
        roles = list(role_to_function_nodes.keys())
        if not roles:
            return

        workers = max(1, min(self.options.iam_workers, len(roles)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_role = {
                pool.submit(self._get_role_policy_documents, session, role_name): role_name
                for role_name in roles
            }
            self._drain_futures(
                future_to_role,
                lambda future, role_name: self._apply_role_policy_dependencies(
                    role_name,
                    future,
                    role_to_function_nodes,
                ),
            )

    def _apply_role_policy_dependencies(
        self,
        role_name: str,
        future: Future[Any],
        role_to_function_nodes: Dict[str, List[str]],
    ) -> None:
        try:
            policy_details = future.result()
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("AccessDenied", "AccessDeniedException"):
                self.store.add_warning(f"[permission] iam: access denied reading policies for role {role_name}")
            else:
                self.store.add_warning(f"iam policy lookup failed for {role_name}: {error_code} - {exc}")
            return
        except Exception as exc:
            logger.warning("IAM policy lookup failed for role %s: %s", role_name, exc)
            self.store.add_warning(f"iam policy lookup failed for {role_name}: {type(exc).__name__}")
            return

        self._ensure_not_cancelled()
        for function_node_id in role_to_function_nodes.get(role_name, []):
            self._apply_policy_dependencies(function_node_id, policy_details)

    def _apply_policy_dependencies(self, function_node_id: str, statements: List[Dict[str, Any]]) -> None:
        for statement in statements:
            self._ensure_not_cancelled()
            effect = str(statement.get("Effect", "Allow")).lower()
            if effect != "allow":
                continue
            actions = [str(action).lower() for action in _safe_list(statement.get("Action"))]
            resources = [str(resource) for resource in _safe_list(statement.get("Resource"))]

            service_hits = self._services_from_actions(actions)
            for service in service_hits:
                for resource in resources or ["*"]:
                    if resource == "*":
                        continue  # wildcard would create meaningless *:* phantom nodes
                    target = self._target_from_service_resource(service, resource)
                    self.store.add_edge(
                        function_node_id,
                        target,
                        relationship="calls",
                        via="lambda_role_policy",
                        actions=sorted(service_hits[service]),
                    )

    def _services_from_actions(self, actions: Iterable[str]) -> Dict[str, Set[str]]:
        service_actions: Dict[str, Set[str]] = {}
        for action in actions:
            if ":" not in action:
                continue
            prefix, verb = action.split(":", 1)
            normalized = self._IAM_PREFIX_TO_SERVICE.get(prefix)
            if normalized:
                service_actions.setdefault(normalized, set()).add(verb)
        return service_actions

    def _target_from_service_resource(self, service: str, resource: str) -> str:
        self._ensure_not_cancelled()
        if resource.startswith("arn:aws:"):
            target = self._add_arn_node(resource, node_type="resource")
            self._node(target, service=service)
            return target
        node_id = self._make_node_id(service, resource)
        self._node(node_id, label=resource, service=service, type="resource", arn=resource)
        return node_id

    def _get_role_policy_documents(
        self,
        session: boto3.session.Session,
        role_name: str,
    ) -> List[Dict[str, Any]]:
        with self._iam_cache_lock:
            cached = self._iam_role_cache.get(role_name)
        if cached is not None:
            return cached

        iam = self._client(session, "iam")
        policy_docs: List[Dict[str, Any]] = []

        inline_policy_names: List[str] = []
        inline_marker: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            inline_kwargs: Dict[str, Any] = {"RoleName": role_name}
            if inline_marker:
                inline_kwargs["Marker"] = inline_marker
            self._increment_api_call("iam", "list_role_policies")
            inline_page = iam.list_role_policies(**inline_kwargs)
            inline_policy_names.extend(inline_page.get("PolicyNames", []))
            inline_marker = inline_page.get("Marker") if inline_page.get("IsTruncated") else None
            if not inline_marker:
                break
        for policy_name in inline_policy_names:
            self._ensure_not_cancelled()
            self._increment_api_call("iam", "get_role_policy")
            raw = iam.get_role_policy(RoleName=role_name, PolicyName=policy_name)
            policy_docs.append(raw.get("PolicyDocument", {}))

        attached_policies: List[Dict[str, Any]] = []
        marker: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {"RoleName": role_name}
            if marker:
                kwargs["Marker"] = marker
            self._increment_api_call("iam", "list_attached_role_policies")
            page = iam.list_attached_role_policies(**kwargs)
            attached_policies.extend(page.get("AttachedPolicies", []))
            marker = page.get("Marker") if page.get("IsTruncated") else None
            if not marker:
                break

        for attached in attached_policies:
            self._ensure_not_cancelled()
            self._increment_api_call("iam", "get_policy")
            policy = iam.get_policy(PolicyArn=attached["PolicyArn"]).get("Policy", {})
            default_version = policy.get("DefaultVersionId")
            if not default_version:
                continue
            self._increment_api_call("iam", "get_policy_version")
            version = iam.get_policy_version(
                PolicyArn=attached["PolicyArn"],
                VersionId=default_version,
            )
            policy_docs.append(version.get("PolicyVersion", {}).get("Document", {}))

        statements: List[Dict[str, Any]] = []
        for document in policy_docs:
            statements.extend(_safe_list(document.get("Statement")))

        with self._iam_cache_lock:
            self._iam_role_cache[role_name] = statements
        return statements

    def _scan_sqs(self, session: boto3.session.Session) -> None:
        client = self._client(session, "sqs")
        queue_urls: List[str] = []
        next_token: Optional[str] = None

        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("sqs", "list_queues")
            page = client.list_queues(**kwargs)
            queue_urls.extend(page.get("QueueUrls", []))
            next_token = page.get("NextToken")
            if not next_token:
                break

        if not self.options.include_resource_describes:
            for queue_url in queue_urls:
                self._ensure_not_cancelled()
                queue_name = queue_url.rstrip("/").split("/")[-1]
                node_id = self._make_node_id("sqs", queue_url)
                self._node(
                    node_id,
                    label=queue_name,
                    service="sqs",
                    type="queue",
                    queue_url=queue_url,
                    arn=queue_url,
                )
            return

        workers = max(1, min(self.options.sqs_attribute_workers, len(queue_urls) or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._fetch_sqs_queue_attributes, client, queue_url): queue_url
                for queue_url in queue_urls
            }
            self._drain_futures(futures, self._apply_sqs_queue_attributes)

    def _fetch_sqs_queue_attributes(self, client: Any, queue_url: str) -> Dict[str, Any]:
        self._increment_api_call("sqs", "get_queue_attributes")
        return client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["QueueArn", "VisibilityTimeout", "CreatedTimestamp", "RedrivePolicy"],
        ).get("Attributes", {})

    def _apply_sqs_queue_attributes(self, future: Future[Any], queue_url: str) -> None:
        try:
            attrs = future.result()
        except Exception as exc:
            logger.debug("Failed to fetch SQS queue attributes for %s: %s", queue_url, exc)
            return
        self._ensure_not_cancelled()
        queue_arn = attrs.get("QueueArn")
        queue_name = queue_url.rstrip("/").split("/")[-1]
        if queue_arn:
            node_id = self._add_arn_node(queue_arn, label=queue_name, node_type="queue")
        else:
            node_id = self._make_node_id("sqs", queue_url)
            self._node(node_id, label=queue_name, service="sqs", type="queue", arn=queue_url)
        self._node(
            node_id,
            queue_url=queue_url,
            visibility_timeout=attrs.get("VisibilityTimeout"),
            created_timestamp=attrs.get("CreatedTimestamp"),
        )
        # SQS → SQS dead-letter queue edge
        redrive_raw = attrs.get("RedrivePolicy")
        if redrive_raw:
            try:
                redrive = json.loads(redrive_raw)
                dlq_arn = redrive.get("deadLetterTargetArn", "")
                if dlq_arn.startswith("arn:aws:"):
                    dlq_name = dlq_arn.split(":")[-1]
                    dlq_node = self._add_arn_node(dlq_arn, label=dlq_name, node_type="queue")
                    self._node(dlq_node, service="sqs")
                    self.store.add_edge(
                        node_id, dlq_node, relationship="dead_letter_to", via="sqs_redrive_policy"
                    )
            except Exception as exc:
                logger.debug("Failed to parse SQS redrive policy: %s", exc)

    def _scan_eventbridge(self, session: boto3.session.Session) -> None:
        client = self._client(session, "events")
        paginator = client.get_paginator("list_rules")
        rules: List[Dict[str, Any]] = []
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("eventbridge", "list_rules")
            rules.extend(page.get("Rules", []))

        for rule in rules:
            self._ensure_not_cancelled()
            rule_arn = rule.get("Arn") or f"rule:{rule.get('Name')}"
            rule_node = self._add_arn_node(rule_arn, label=rule.get("Name"), node_type="rule")
            self._node(
                rule_node,
                service="eventbridge",
                event_pattern=rule.get("EventPattern"),
                state=rule.get("State"),
                schedule_expression=rule.get("ScheduleExpression"),
            )

        workers = max(1, min(self.options.eventbridge_target_workers, len(rules) or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._fetch_eventbridge_targets, client, rule): rule for rule in rules}
            self._drain_futures(futures, self._apply_eventbridge_targets)

    def _fetch_eventbridge_targets(self, client: Any, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        targets: List[Dict[str, Any]] = []
        next_token: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {"Rule": rule["Name"]}
            if rule.get("EventBusName"):
                kwargs["EventBusName"] = rule["EventBusName"]
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("eventbridge", "list_targets_by_rule")
            page = client.list_targets_by_rule(**kwargs)
            targets.extend(page.get("Targets", []))
            next_token = page.get("NextToken")
            if not next_token:
                break
        return targets

    def _apply_eventbridge_targets(self, future: Future[Any], rule: Dict[str, Any]) -> None:
        try:
            targets = future.result()
        except Exception as exc:
            logger.debug("Failed to fetch EventBridge targets: %s", exc)
            return
        self._ensure_not_cancelled()
        rule_arn = rule.get("Arn") or f"rule:{rule.get('Name')}"
        # Mirror the same node ID construction used in _scan_eventbridge
        rule_node = self._add_arn_node(rule_arn, label=rule.get("Name"), node_type="rule")
        for target in targets:
            self._ensure_not_cancelled()
            target_arn = target.get("Arn")
            if not target_arn:
                continue
            target_node = self._add_arn_node(target_arn)
            self.store.add_edge(
                rule_node,
                target_node,
                relationship="triggers",
                via="eventbridge_rule_target",
                target_id=target.get("Id"),
            )

    def _scan_dynamodb(self, session: boto3.session.Session) -> None:
        client = self._client(session, "dynamodb")
        table_names: List[str] = []
        table_name: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if table_name:
                kwargs["ExclusiveStartTableName"] = table_name
            self._increment_api_call("dynamodb", "list_tables")
            page = client.list_tables(**kwargs)
            table_names.extend(page.get("TableNames", []))
            table_name = page.get("LastEvaluatedTableName")
            if not table_name:
                break

        if not self.options.include_resource_describes:
            for name in table_names:
                self._ensure_not_cancelled()
                node_id = self._make_node_id("dynamodb", name)
                self._node(node_id, label=name, service="dynamodb", type="table", arn=name)
            return

        workers = max(1, min(self.options.dynamodb_describe_workers, len(table_names) or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._describe_table, client, name): name for name in table_names}
            self._drain_futures(futures, self._apply_described_table)

    def _describe_table(self, client: Any, table_name: str) -> Dict[str, Any]:
        self._increment_api_call("dynamodb", "describe_table")
        return client.describe_table(TableName=table_name).get("Table", {})

    def _apply_described_table(self, future: Future[Any], table_name: str) -> None:
        try:
            table = future.result()
        except Exception as exc:
            logger.warning("DynamoDB describe_table failed for %s: %s", table_name, exc)
            self.store.add_warning(f"dynamodb describe failed for {table_name}: {type(exc).__name__} - {exc}")
            return
        self._ensure_not_cancelled()
        table_arn = table.get("TableArn", f"dynamodb:{table_name}")
        node_id = self._add_arn_node(table_arn, label=table_name, node_type="table")
        self._node(
            node_id,
            service="dynamodb",
            item_count=table.get("ItemCount"),
            table_size_bytes=table.get("TableSizeBytes"),
            stream_arn=table.get("LatestStreamArn"),
            billing_mode=(table.get("BillingModeSummary") or {}).get("BillingMode"),
            state=table.get("TableStatus"),
        )

        # Phase 3, Item 8: DynamoDB Streams explicit edge
        stream_arn = table.get("LatestStreamArn")
        if stream_arn and _ARN_PATTERN.match(stream_arn):
            stream_node = self._add_arn_node(stream_arn, label=f"{table_name}-stream", node_type="stream")
            self._node(stream_node, service="dynamodb", type="stream")
            self.store.add_edge(node_id, stream_node, relationship="streams_to", via="dynamodb_stream")

        # DynamoDB global table replicas
        for replica in table.get("Replicas", []):
            replica_region = replica.get("RegionName", "")
            if replica_region and replica_region != self._region:
                replica_node = self._make_node_id("dynamodb", f"{table_name}@{replica_region}")
                self._node(replica_node, label=f"{table_name} ({replica_region})", service="dynamodb",
                           type="table_replica", region=replica_region,
                           state=replica.get("ReplicaStatus"))
                self.store.add_edge(node_id, replica_node, relationship="replicates_to",
                                    via="dynamodb_global_table")

    # ── EC2 ──────────────────────────────────────────────────────────────────

    def _scan_ec2(self, session: boto3.session.Session) -> None:
        client = self._client(session, "ec2")
        paginator = client.get_paginator("describe_instances")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_instances")
            for reservation in page.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    self._ensure_not_cancelled()
                    instance_id = instance.get("InstanceId", "")
                    owner_id = instance.get("OwnerId", "")
                    name_tag = next((t["Value"] for t in instance.get("Tags", []) if t.get("Key") == "Name"), None)
                    arn = f"arn:aws:ec2:{self._region}:{owner_id}:instance/{instance_id}"
                    node_id = self._make_node_id("ec2", instance_id)
                    self._node(
                        node_id,
                        label=name_tag or instance_id,
                        service="ec2",
                        type="instance",
                        arn=arn,
                        instance_type=instance.get("InstanceType"),
                        state=instance.get("State", {}).get("Name"),
                        vpc_id=instance.get("VpcId"),
                        subnet_id=instance.get("SubnetId"),
                    )

                    # Phase 2, Item 5: EC2 → VPC / Subnet / Security Group edges
                    vpc_id = instance.get("VpcId")
                    if vpc_id:
                        vpc_node = self._make_node_id("vpc", f"vpc/{vpc_id}")
                        self._node(vpc_node, label=vpc_id, service="vpc", type="vpc",
                                   arn=f"arn:aws:ec2:{self._region}:{owner_id}:vpc/{vpc_id}")
                        self.store.add_edge(vpc_node, node_id, relationship="contains", via="ec2_vpc_membership")

                    subnet_id = instance.get("SubnetId")
                    if subnet_id:
                        subnet_node = self._make_node_id("vpc", f"subnet/{subnet_id}")
                        self._node(subnet_node, label=subnet_id, service="vpc", type="subnet",
                                   arn=f"arn:aws:ec2:{self._region}:{owner_id}:subnet/{subnet_id}")
                        self.store.add_edge(subnet_node, node_id, relationship="contains", via="ec2_subnet_membership")
                        if vpc_id:
                            self.store.add_edge(vpc_node, subnet_node, relationship="contains", via="ec2_vpc_subnet")

                    for sg in instance.get("SecurityGroups", []):
                        sg_id = sg.get("GroupId", "")
                        if sg_id:
                            sg_node = self._make_node_id("vpc", f"sg/{sg_id}")
                            self._node(sg_node, label=sg.get("GroupName", sg_id), service="vpc",
                                       type="security_group",
                                       arn=f"arn:aws:ec2:{self._region}:{owner_id}:security-group/{sg_id}")
                            self.store.add_edge(sg_node, node_id, relationship="protects", via="ec2_security_group")

                    # EC2 → IAM Instance Profile
                    iam_profile = instance.get("IamInstanceProfile", {})
                    profile_arn = iam_profile.get("Arn", "")
                    if profile_arn and _ARN_PATTERN.match(profile_arn):
                        profile_node = self._add_arn_node(profile_arn, label=profile_arn.split("/")[-1],
                                                          node_type="instance_profile")
                        self._node(profile_node, service="iam")
                        self.store.add_edge(profile_node, node_id, relationship="assumed_by",
                                            via="ec2_instance_profile")

    # ── VPC Network Topology ────────────────────────────────────────────────

    def _scan_vpc(self, session: boto3.session.Session, *, vpc_ids: Optional[Set[str]] = None) -> None:
        client = self._client(session, "ec2")
        vpc_id_list = list(vpc_ids) if vpc_ids else None

        # 1. VPCs
        vpc_kwargs = {"VpcIds": vpc_id_list} if vpc_id_list else {}
        for page in client.get_paginator("describe_vpcs").paginate(**vpc_kwargs):
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_vpcs")
            for vpc in page.get("Vpcs", []):
                self._ensure_not_cancelled()
                vpc_id = vpc["VpcId"]
                owner_id = vpc.get("OwnerId", "")
                name_tag = next((t["Value"] for t in vpc.get("Tags", []) if t.get("Key") == "Name"), None)
                node_id = self._make_node_id("vpc", f"vpc/{vpc_id}")
                self._node(
                    node_id,
                    label=name_tag or vpc_id,
                    service="vpc",
                    type="vpc",
                    arn=f"arn:aws:ec2:{self._region}:{owner_id}:vpc/{vpc_id}",
                    cidr_block=vpc.get("CidrBlock"),
                    is_default=vpc.get("IsDefault", False),
                    state=vpc.get("State"),
                )

        # 2. Subnets
        subnet_kwargs = {"Filters": [{"Name": "vpc-id", "Values": vpc_id_list}]} if vpc_id_list else {}
        for page in client.get_paginator("describe_subnets").paginate(**subnet_kwargs):
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_subnets")
            for subnet in page.get("Subnets", []):
                self._ensure_not_cancelled()
                subnet_id = subnet["SubnetId"]
                vpc_id = subnet.get("VpcId")
                owner_id = subnet.get("OwnerId", "")
                name_tag = next((t["Value"] for t in subnet.get("Tags", []) if t.get("Key") == "Name"), None)
                node_id = self._make_node_id("vpc", f"subnet/{subnet_id}")
                self._node(
                    node_id,
                    label=name_tag or subnet_id,
                    service="vpc",
                    type="subnet",
                    arn=f"arn:aws:ec2:{self._region}:{owner_id}:subnet/{subnet_id}",
                    cidr_block=subnet.get("CidrBlock"),
                    availability_zone=subnet.get("AvailabilityZone"),
                    map_public_ip_on_launch=subnet.get("MapPublicIpOnLaunch", False),
                    available_ip_count=subnet.get("AvailableIpAddressCount"),
                )
                if vpc_id:
                    vpc_node = self._make_node_id("vpc", f"vpc/{vpc_id}")
                    self.store.add_edge(vpc_node, node_id, relationship="contains", via="vpc_subnet")

        # 3. Security Groups
        # Accumulate SG rule edges to handle DiGraph single-edge constraint
        pending_sg_edges: Dict[tuple, List[str]] = {}  # (src, tgt) -> list of port labels
        sg_vpc_map: Dict[str, str] = {}  # sg_node_id -> vpc_id

        sg_kwargs = {"Filters": [{"Name": "vpc-id", "Values": vpc_id_list}]} if vpc_id_list else {}
        for page in client.get_paginator("describe_security_groups").paginate(**sg_kwargs):
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_security_groups")
            for sg in page.get("SecurityGroups", []):
                self._ensure_not_cancelled()
                sg_id = sg["GroupId"]
                vpc_id = sg.get("VpcId")
                owner_id = sg.get("OwnerId", "")
                inbound_rules = sg.get("IpPermissions", [])
                outbound_rules = sg.get("IpPermissionsEgress", [])
                has_open_ingress = any(
                    any(r.get("CidrIp") == "0.0.0.0/0" for r in perm.get("IpRanges", [])) or
                    any(r.get("CidrIpv6") == "::/0" for r in perm.get("Ipv6Ranges", []))
                    for perm in inbound_rules
                )
                node_id = self._make_node_id("vpc", f"sg/{sg_id}")

                # Parse rules for tooltip display and edge creation
                parsed_in = self._parse_sg_rules(inbound_rules)
                parsed_out = self._parse_sg_rules(outbound_rules)

                self._node(
                    node_id,
                    label=sg.get("GroupName", sg_id),
                    service="vpc",
                    type="security_group",
                    arn=f"arn:aws:ec2:{self._region}:{owner_id}:security-group/{sg_id}",
                    group_name=sg.get("GroupName"),
                    description=sg.get("Description"),
                    inbound_rule_count=len(inbound_rules),
                    outbound_rule_count=len(outbound_rules),
                    has_open_ingress=has_open_ingress,
                    inbound_rules_parsed=parsed_in,
                    outbound_rules_parsed=parsed_out,
                )
                if vpc_id:
                    vpc_node = self._make_node_id("vpc", f"vpc/{vpc_id}")
                    self.store.add_edge(vpc_node, node_id, relationship="contains", via="vpc_security_group")
                    sg_vpc_map[node_id] = vpc_id

                # Accumulate SG rule edges from inbound rules
                for rule in parsed_in:
                    port_label = rule["port_range"]
                    if rule["source_type"] == "cidr" and rule["source"] in ("0.0.0.0/0", "::/0"):
                        # Will connect from Internet node later (after IGW scan)
                        if vpc_id:
                            internet_key = ("__internet__", vpc_id, node_id)
                            pending_sg_edges.setdefault(internet_key, []).append(port_label)
                    elif rule["source_type"] == "sg":
                        ref_sg_node = self._make_node_id("vpc", f"sg/{rule['source']}")
                        pending_sg_edges.setdefault((ref_sg_node, node_id), []).append(port_label)

        # 4. Internet Gateways
        vpc_to_igws: Dict[str, List[str]] = {}  # vpc_id -> list of igw node IDs

        igw_kwargs = {"Filters": [{"Name": "attachment.vpc-id", "Values": vpc_id_list}]} if vpc_id_list else {}
        for page in client.get_paginator("describe_internet_gateways").paginate(**igw_kwargs):
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_internet_gateways")
            for igw in page.get("InternetGateways", []):
                self._ensure_not_cancelled()
                igw_id = igw["InternetGatewayId"]
                owner_id = igw.get("OwnerId", "")
                name_tag = next((t["Value"] for t in igw.get("Tags", []) if t.get("Key") == "Name"), None)
                node_id = self._make_node_id("vpc", f"igw/{igw_id}")
                self._node(
                    node_id,
                    label=name_tag or igw_id,
                    service="vpc",
                    type="internet_gateway",
                    arn=f"arn:aws:ec2:{self._region}:{owner_id}:internet-gateway/{igw_id}",
                )
                for attachment in igw.get("Attachments", []):
                    att_vpc_id = attachment.get("VpcId")
                    if att_vpc_id:
                        vpc_node = self._make_node_id("vpc", f"vpc/{att_vpc_id}")
                        self.store.add_edge(node_id, vpc_node, relationship="attached_to", via="igw_attachment")
                        vpc_to_igws.setdefault(att_vpc_id, []).append(node_id)

        # 4b. Internet Anchor Nodes — one per VPC with an IGW
        for vpc_id, igw_node_ids in vpc_to_igws.items():
            internet_node = self._make_node_id("vpc", f"internet/{vpc_id}")
            self._node(
                internet_node,
                label="Internet",
                service="vpc",
                type="internet",
            )
            for igw_node_id in igw_node_ids:
                self.store.add_edge(internet_node, igw_node_id, relationship="gateway", via="internet_igw")

        # 4c. Emit accumulated SG rule edges
        for key, port_labels in pending_sg_edges.items():
            if isinstance(key[0], str) and key[0] == "__internet__":
                # Internet -> SG edge: resolve to the Internet anchor node for this VPC
                _, vpc_id, sg_node = key
                internet_node = self._make_node_id("vpc", f"internet/{vpc_id}")
                self.store.add_edge(
                    internet_node, sg_node,
                    relationship="allows", via="sg_rule",
                    port_range=", ".join(sorted(set(port_labels))),
                )
            else:
                src_node, tgt_node = key
                self.store.add_edge(
                    src_node, tgt_node,
                    relationship="allows", via="sg_rule",
                    port_range=", ".join(sorted(set(port_labels))),
                )

        # 5. NAT Gateways
        nat_kwargs = {"Filters": [{"Name": "vpc-id", "Values": vpc_id_list}]} if vpc_id_list else {}
        for page in client.get_paginator("describe_nat_gateways").paginate(**nat_kwargs):
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_nat_gateways")
            for nat in page.get("NatGateways", []):
                self._ensure_not_cancelled()
                nat_id = nat["NatGatewayId"]
                nat_subnet_id = nat.get("SubnetId")
                owner_id = nat.get("OwnerId", "")
                name_tag = next((t["Value"] for t in nat.get("Tags", []) if t.get("Key") == "Name"), None)
                node_id = self._make_node_id("vpc", f"nat/{nat_id}")
                self._node(
                    node_id,
                    label=name_tag or nat_id,
                    service="vpc",
                    type="nat_gateway",
                    arn=f"arn:aws:ec2:{self._region}:{owner_id}:natgateway/{nat_id}",
                    state=nat.get("State"),
                    connectivity_type=nat.get("ConnectivityType"),
                    subnet_id=nat_subnet_id,
                )
                if nat_subnet_id:
                    subnet_node = self._make_node_id("vpc", f"subnet/{nat_subnet_id}")
                    self.store.add_edge(subnet_node, node_id, relationship="contains", via="subnet_nat_gateway")

        # 6. Route Tables
        rtb_kwargs = {"Filters": [{"Name": "vpc-id", "Values": vpc_id_list}]} if vpc_id_list else {}
        for page in client.get_paginator("describe_route_tables").paginate(**rtb_kwargs):
            self._ensure_not_cancelled()
            self._increment_api_call("ec2", "describe_route_tables")
            for rtb in page.get("RouteTables", []):
                self._ensure_not_cancelled()
                rtb_id = rtb["RouteTableId"]
                rtb_vpc_id = rtb.get("VpcId")
                owner_id = rtb.get("OwnerId", "")
                name_tag = next((t["Value"] for t in rtb.get("Tags", []) if t.get("Key") == "Name"), None)
                is_main = any(a.get("Main", False) for a in rtb.get("Associations", []))
                node_id = self._make_node_id("vpc", f"rtb/{rtb_id}")
                self._node(
                    node_id,
                    label=name_tag or rtb_id,
                    service="vpc",
                    type="route_table",
                    arn=f"arn:aws:ec2:{self._region}:{owner_id}:route-table/{rtb_id}",
                    is_main=is_main,
                )
                if rtb_vpc_id:
                    vpc_node = self._make_node_id("vpc", f"vpc/{rtb_vpc_id}")
                    self.store.add_edge(vpc_node, node_id, relationship="contains", via="vpc_route_table")

                # Subnet associations
                for assoc in rtb.get("Associations", []):
                    assoc_subnet = assoc.get("SubnetId")
                    if assoc_subnet:
                        subnet_node = self._make_node_id("vpc", f"subnet/{assoc_subnet}")
                        self.store.add_edge(node_id, subnet_node, relationship="routes", via="rtb_subnet_association")

                # Route targets (IGW and NAT)
                for route in rtb.get("Routes", []):
                    gw_id = route.get("GatewayId", "")
                    if gw_id.startswith("igw-"):
                        igw_node = self._make_node_id("vpc", f"igw/{gw_id}")
                        self.store.add_edge(igw_node, node_id, relationship="routes_via", via="igw_route")
                    nat_gw_id = route.get("NatGatewayId", "")
                    if nat_gw_id:
                        nat_node = self._make_node_id("vpc", f"nat/{nat_gw_id}")
                        self.store.add_edge(nat_node, node_id, relationship="routes_via", via="nat_route")

    # ── ECS ──────────────────────────────────────────────────────────────────

    def _scan_ecs(self, session: boto3.session.Session) -> None:
        client = self._client(session, "ecs")
        cluster_arns: List[str] = []
        paginator = client.get_paginator("list_clusters")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("ecs", "list_clusters")
            cluster_arns.extend(page.get("clusterArns", []))

        for arn in cluster_arns:
            self._ensure_not_cancelled()
            cluster_name = arn.split("/")[-1]
            cluster_node = self._add_arn_node(arn, label=cluster_name, node_type="cluster")
            self._node(cluster_node, service="ecs")

            # List services in this cluster
            svc_arns: List[str] = []
            svc_paginator = client.get_paginator("list_services")
            for svc_page in svc_paginator.paginate(cluster=arn):
                self._ensure_not_cancelled()
                self._increment_api_call("ecs", "list_services")
                svc_arns.extend(svc_page.get("serviceArns", []))

            for svc_arn in svc_arns:
                self._ensure_not_cancelled()
                svc_name = svc_arn.split("/")[-1]
                svc_node = self._add_arn_node(svc_arn, label=svc_name, node_type="service")
                self._node(svc_node, service="ecs")
                self.store.add_edge(cluster_node, svc_node, relationship="hosts")

            # Phase 2, Item 6: ECS describe_services for task def, LB, and role edges
            if svc_arns and self.options.include_resource_describes:
                self._describe_ecs_service_edges(client, arn, svc_arns)

    def _describe_ecs_service_edges(self, client: Any, cluster_arn: str, service_arns: List[str]) -> None:
        """Enrich ECS services with task definition, load balancer, and role edges."""
        # describe_services accepts max 10 at a time
        for batch_start in range(0, len(service_arns), 10):
            self._ensure_not_cancelled()
            batch = service_arns[batch_start:batch_start + 10]
            try:
                self._increment_api_call("ecs", "describe_services")
                response = client.describe_services(cluster=cluster_arn, services=batch)
            except (ClientError, BotoCoreError) as exc:
                logger.debug("ECS describe_services failed for cluster %s: %s", cluster_arn.split("/")[-1], exc)
                continue

            for svc in response.get("services", []):
                svc_arn = svc.get("serviceArn", "")
                svc_node = self._make_node_id(self._service_from_arn(svc_arn), svc_arn) if svc_arn else None
                if not svc_node:
                    continue

                # Task definition edge
                task_def_arn = svc.get("taskDefinition", "")
                if task_def_arn and _ARN_PATTERN.match(task_def_arn):
                    td_node = self._add_arn_node(task_def_arn, label=task_def_arn.split("/")[-1],
                                                 node_type="task_definition")
                    self._node(td_node, service="ecs")
                    self.store.add_edge(svc_node, td_node, relationship="uses", via="ecs_task_definition")

                # Load balancer / target group edges
                for lb in svc.get("loadBalancers", []):
                    tg_arn = lb.get("targetGroupArn", "")
                    if tg_arn and _ARN_PATTERN.match(tg_arn):
                        tg_node = self._add_arn_node(tg_arn, label=tg_arn.split("/")[-1],
                                                     node_type="target_group")
                        self._node(tg_node, service="elb")
                        self.store.add_edge(svc_node, tg_node, relationship="registered_with",
                                            via="ecs_load_balancer")

                # Service role edge
                role_arn = svc.get("roleArn", "")
                if role_arn and _ARN_PATTERN.match(role_arn):
                    role_node = self._add_arn_node(role_arn, label=role_arn.split("/")[-1], node_type="role")
                    self._node(role_node, service="iam")
                    self.store.add_edge(role_node, svc_node, relationship="assumed_by",
                                        via="ecs_service_role")

                # ECS VPC topology edges
                net_config = svc.get("networkConfiguration", {}).get("awsvpcConfiguration", {})
                for ecs_subnet_id in net_config.get("subnets", []):
                    ecs_subnet_node = self._make_node_id("vpc", f"subnet/{ecs_subnet_id}")
                    self._node(ecs_subnet_node, label=ecs_subnet_id, service="vpc", type="subnet")
                    self.store.add_edge(ecs_subnet_node, svc_node, relationship="contains", via="ecs_vpc_placement")
                for ecs_sg_id in net_config.get("securityGroups", []):
                    ecs_sg_node = self._make_node_id("vpc", f"sg/{ecs_sg_id}")
                    self._node(ecs_sg_node, label=ecs_sg_id, service="vpc", type="security_group")
                    self.store.add_edge(ecs_sg_node, svc_node, relationship="protects", via="ecs_security_group")

    # ── S3 ───────────────────────────────────────────────────────────────────

    def _scan_s3(self, session: boto3.session.Session) -> None:
        client = self._client(session, "s3")
        self._increment_api_call("s3", "list_buckets")
        response = client.list_buckets()
        bucket_nodes: Dict[str, str] = {}  # bucket_name -> node_id

        for bucket in response.get("Buckets", []):
            self._ensure_not_cancelled()
            name = bucket.get("Name", "")
            arn = f"arn:aws:s3:::{name}"
            node_id = self._make_node_id("s3", name)
            self._node(
                node_id,
                label=name,
                service="s3",
                type="bucket",
                arn=arn,
                creation_date=str(bucket.get("CreationDate", "")),
            )
            bucket_nodes[name] = node_id

        # S3 → Lambda / SQS / SNS (bucket event notifications)
        if bucket_nodes:
            workers = max(1, min(16, len(bucket_nodes)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._fetch_s3_notifications, client, name): node_id
                    for name, node_id in bucket_nodes.items()
                }
                self._drain_futures(futures, self._apply_s3_notifications)

    def _fetch_s3_notifications(self, client: Any, bucket_name: str) -> Dict[str, Any]:
        try:
            self._increment_api_call("s3", "get_bucket_notification_configuration")
            return client.get_bucket_notification_configuration(Bucket=bucket_name)
        except (ClientError, BotoCoreError) as exc:
            logger.debug("S3 notification fetch failed for %s: %s", bucket_name, exc)
            return {}

    def _apply_s3_notifications(self, future: Future[Any], bucket_node: str) -> None:
        try:
            config = future.result()
        except Exception:
            return
        self._ensure_not_cancelled()
        # Lambda notifications
        for notif in config.get("LambdaFunctionConfigurations", []):
            target_arn = notif.get("LambdaFunctionArn", "")
            if target_arn.startswith("arn:aws:"):
                target_node = self._add_arn_node(target_arn)
                self.store.add_edge(
                    bucket_node, target_node, relationship="triggers", via="s3_notification"
                )
        # SQS notifications
        for notif in config.get("QueueConfigurations", []):
            target_arn = notif.get("QueueArn", "")
            if target_arn.startswith("arn:aws:"):
                target_node = self._add_arn_node(target_arn)
                self.store.add_edge(
                    bucket_node, target_node, relationship="triggers", via="s3_notification"
                )
        # SNS notifications
        for notif in config.get("TopicConfigurations", []):
            target_arn = notif.get("TopicArn", "")
            if target_arn.startswith("arn:aws:"):
                target_node = self._add_arn_node(target_arn)
                self.store.add_edge(
                    bucket_node, target_node, relationship="triggers", via="s3_notification"
                )

    # ── RDS ──────────────────────────────────────────────────────────────────

    def _scan_rds(self, session: boto3.session.Session) -> None:
        client = self._client(session, "rds")
        cluster_nodes: Dict[str, str] = {}  # DBClusterIdentifier -> node_id
        instance_cluster_map: List[tuple[str, str]] = []  # (instance_node_id, cluster_identifier)

        # Instances
        paginator = client.get_paginator("describe_db_instances")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("rds", "describe_db_instances")
            for db in page.get("DBInstances", []):
                self._ensure_not_cancelled()
                arn = db.get("DBInstanceArn", "")
                node_id = self._add_arn_node(arn, label=db.get("DBInstanceIdentifier"), node_type="instance")
                self._node(
                    node_id,
                    service="rds",
                    engine=db.get("Engine"),
                    instance_class=db.get("DBInstanceClass"),
                    state=db.get("DBInstanceStatus"),
                    multi_az=db.get("MultiAZ"),
                )
                # RDS VPC topology edges
                subnet_group = db.get("DBSubnetGroup", {})
                rds_vpc_id = subnet_group.get("VpcId")
                if rds_vpc_id:
                    rds_vpc_node = self._make_node_id("vpc", f"vpc/{rds_vpc_id}")
                    self._node(rds_vpc_node, label=rds_vpc_id, service="vpc", type="vpc")
                    self.store.add_edge(rds_vpc_node, node_id, relationship="contains", via="rds_vpc_membership")
                    for rds_subnet in subnet_group.get("Subnets", []):
                        rds_subnet_id = rds_subnet.get("SubnetIdentifier")
                        if rds_subnet_id:
                            rds_subnet_node = self._make_node_id("vpc", f"subnet/{rds_subnet_id}")
                            self._node(rds_subnet_node, label=rds_subnet_id, service="vpc", type="subnet")
                            self.store.add_edge(rds_subnet_node, node_id, relationship="contains", via="rds_subnet_placement")
                for vsg in db.get("VpcSecurityGroups", []):
                    rds_sg_id = vsg.get("VpcSecurityGroupId")
                    if rds_sg_id:
                        rds_sg_node = self._make_node_id("vpc", f"sg/{rds_sg_id}")
                        self._node(rds_sg_node, label=rds_sg_id, service="vpc", type="security_group")
                        self.store.add_edge(rds_sg_node, node_id, relationship="protects", via="rds_security_group")

                cluster_id = db.get("DBClusterIdentifier")
                if cluster_id:
                    instance_cluster_map.append((node_id, cluster_id))

        # Aurora clusters
        try:
            cluster_paginator = client.get_paginator("describe_db_clusters")
            for page in cluster_paginator.paginate():
                self._ensure_not_cancelled()
                self._increment_api_call("rds", "describe_db_clusters")
                for cluster in page.get("DBClusters", []):
                    self._ensure_not_cancelled()
                    arn = cluster.get("DBClusterArn", "")
                    cluster_id = cluster.get("DBClusterIdentifier", "")
                    node_id = self._add_arn_node(arn, label=cluster_id, node_type="cluster")
                    self._node(
                        node_id,
                        service="rds",
                        engine=cluster.get("Engine"),
                        state=cluster.get("Status"),
                    )
                    cluster_nodes[cluster_id] = node_id
        except (ClientError, BotoCoreError) as exc:
            logger.debug("RDS cluster scan skipped: %s", exc)

        # RDS cluster → instance edges
        for instance_node, cluster_id in instance_cluster_map:
            cluster_node = cluster_nodes.get(cluster_id)
            if cluster_node:
                self.store.add_edge(
                    cluster_node, instance_node, relationship="contains", via="rds_cluster_member"
                )

    # ── Step Functions ───────────────────────────────────────────────────────

    def _scan_stepfunctions(self, session: boto3.session.Session) -> None:
        client = self._client(session, "stepfunctions")
        sm_arns: List[tuple[str, str]] = []  # (arn, node_id)

        paginator = client.get_paginator("list_state_machines")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("stepfunctions", "list_state_machines")
            for sm in page.get("stateMachines", []):
                self._ensure_not_cancelled()
                arn = sm.get("stateMachineArn", "")
                node_id = self._add_arn_node(arn, label=sm.get("name"), node_type="state_machine")
                self._node(
                    node_id,
                    service="stepfunctions",
                    sm_type=sm.get("type"),
                    creation_date=str(sm.get("creationDate", "")),
                )
                sm_arns.append((arn, node_id))

        # Step Functions → Lambda / ECS / DynamoDB / SQS / SNS (ASL task resources)
        if sm_arns:
            workers = max(1, min(8, len(sm_arns)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._fetch_sfn_definition, client, arn): node_id
                    for arn, node_id in sm_arns
                }
                self._drain_futures(futures, self._apply_sfn_edges)

    def _fetch_sfn_definition(self, client: Any, arn: str) -> Optional[str]:
        try:
            self._increment_api_call("stepfunctions", "describe_state_machine")
            return client.describe_state_machine(stateMachineArn=arn).get("definition")
        except (ClientError, BotoCoreError) as exc:
            logger.debug("Step Functions describe failed for %s: %s", arn, exc)
            return None

    def _apply_sfn_edges(self, future: Future[Any], sm_node: str) -> None:
        try:
            definition_str = future.result()
        except Exception:
            return
        if not definition_str:
            return
        self._ensure_not_cancelled()
        try:
            definition = json.loads(definition_str)
        except Exception:
            return

        # Walk all states and extract Task resource ARNs
        states = definition.get("States", {})
        self._extract_sfn_state_edges(sm_node, states)

    def _extract_sfn_state_edges(self, sm_node: str, states: Dict[str, Any]) -> None:
        """Recursively traverse Step Functions states to find Task resource ARNs."""
        for state_name, state in states.items():
            self._ensure_not_cancelled()
            state_type = state.get("Type", "")

            if state_type == "Task":
                resource = state.get("Resource", "")
                params = state.get("Parameters", {})
                self._apply_sfn_task_edge(sm_node, resource, params)

            # Recurse into Parallel branches
            for branch in state.get("Branches", []):
                self._extract_sfn_state_edges(sm_node, branch.get("States", {}))

            # Recurse into Map iterator
            iterator = state.get("Iterator") or state.get("ItemProcessor", {})
            if iterator:
                self._extract_sfn_state_edges(sm_node, iterator.get("States", {}))

    def _apply_sfn_task_edge(self, sm_node: str, resource: str, params: Dict[str, Any]) -> None:
        """Resolve a Step Functions Task resource to a target node and add an edge."""
        if not resource:
            return

        # Direct Lambda ARN: arn:aws:lambda:...
        if ":lambda:" in resource and ":function:" in resource:
            target = self._add_arn_node(resource.split(":$")[0])
            self.store.add_edge(sm_node, target, relationship="invokes", via="sfn_task")
            return

        # Optimised integrations: arn:aws:states:::lambda:invoke
        if "states:::lambda" in resource:
            fn_arn = (params.get("FunctionName") or params.get("FunctionName.$", "")).split(":$")[0]
            if fn_arn.startswith("arn:aws:lambda:"):
                target = self._add_arn_node(fn_arn)
                self.store.add_edge(sm_node, target, relationship="invokes", via="sfn_task")
            return

        if "states:::dynamodb" in resource:
            table_name = params.get("TableName") or params.get("TableName.$", "")
            if table_name and not table_name.startswith("$"):
                node_id = self._make_node_id("dynamodb", table_name)
                self._node(node_id, label=table_name, service="dynamodb", type="table", arn=table_name)
                self.store.add_edge(sm_node, node_id, relationship="reads_writes", via="sfn_task")
            return

        if "states:::sqs" in resource:
            queue_url = params.get("QueueUrl") or params.get("QueueUrl.$", "")
            if queue_url and not queue_url.startswith("$"):
                node_id = self._make_node_id("sqs", queue_url)
                self._node(node_id, label=queue_url.split("/")[-1], service="sqs", type="queue", arn=queue_url)
                self.store.add_edge(sm_node, node_id, relationship="sends_to", via="sfn_task")
            return

        if "states:::sns" in resource:
            topic_arn = params.get("TopicArn") or params.get("TopicArn.$", "")
            if topic_arn and topic_arn.startswith("arn:aws:sns:"):
                target = self._add_arn_node(topic_arn)
                self.store.add_edge(sm_node, target, relationship="publishes_to", via="sfn_task")
            return

        if "states:::ecs" in resource:
            task_def = (params.get("TaskDefinition") or "").split(":")[0]
            cluster_arn = params.get("Cluster", "")
            if cluster_arn.startswith("arn:aws:ecs:"):
                target = self._add_arn_node(cluster_arn)
                self.store.add_edge(sm_node, target, relationship="runs_task", via="sfn_task")
            return

        if "states:::glue" in resource:
            job_name = params.get("JobName") or params.get("JobName.$", "")
            if job_name and not job_name.startswith("$"):
                node_id = self._make_node_id("glue", job_name)
                self._node(node_id, label=job_name, service="glue", type="job",
                            arn=f"arn:aws:glue:{self._region}:*:job/{job_name}")
                self.store.add_edge(sm_node, node_id, relationship="runs_job", via="sfn_task")
            return

        if "states:::states:startExecution" in resource:
            child_arn = params.get("StateMachineArn") or params.get("StateMachineArn.$", "")
            if child_arn and child_arn.startswith("arn:aws:states:"):
                target = self._add_arn_node(child_arn)
                self.store.add_edge(sm_node, target, relationship="starts", via="sfn_task")

    # ── SNS ──────────────────────────────────────────────────────────────────

    def _scan_sns(self, session: boto3.session.Session) -> None:
        client = self._client(session, "sns")
        topic_nodes: Dict[str, str] = {}  # topic_arn -> node_id

        paginator = client.get_paginator("list_topics")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("sns", "list_topics")
            for topic in page.get("Topics", []):
                self._ensure_not_cancelled()
                arn = topic.get("TopicArn", "")
                topic_name = arn.split(":")[-1]
                node_id = self._add_arn_node(arn, label=topic_name, node_type="topic")
                self._node(node_id, service="sns")
                topic_nodes[arn] = node_id

        # SNS → Lambda / SQS / SNS (subscriptions)
        try:
            sub_paginator = client.get_paginator("list_subscriptions")
            for page in sub_paginator.paginate():
                self._ensure_not_cancelled()
                self._increment_api_call("sns", "list_subscriptions")
                for sub in page.get("Subscriptions", []):
                    self._ensure_not_cancelled()
                    topic_arn = sub.get("TopicArn", "")
                    endpoint = sub.get("Endpoint", "")
                    protocol = sub.get("Protocol", "")
                    # Skip pending confirmations and non-ARN endpoints (email, http, sms)
                    if not topic_arn or not endpoint.startswith("arn:aws:"):
                        continue
                    topic_node = topic_nodes.get(topic_arn) or self._add_arn_node(
                        topic_arn, node_type="topic"
                    )
                    target_node = self._add_arn_node(endpoint)
                    self.store.add_edge(
                        topic_node,
                        target_node,
                        relationship="notifies",
                        via="sns_subscription",
                        protocol=protocol,
                    )
        except (ClientError, BotoCoreError) as exc:
            logger.debug("SNS subscription scan skipped: %s", exc)

    # ── Kinesis ──────────────────────────────────────────────────────────────

    def _scan_kinesis(self, session: boto3.session.Session) -> None:
        client = self._client(session, "kinesis")
        stream_names: List[str] = []
        next_token: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("kinesis", "list_streams")
            page = client.list_streams(Limit=100, **kwargs)
            stream_names.extend(page.get("StreamNames", []))
            next_token = page.get("NextToken")
            if not next_token:
                break

        for name in stream_names:
            self._ensure_not_cancelled()
            arn = f"arn:aws:kinesis:{self._region}:{self._account_id}:stream/{name}"
            node_id = self._make_node_id("kinesis", name)
            self._node(node_id, label=name, service="kinesis", type="stream", arn=arn)

    # ── IAM ──────────────────────────────────────────────────────────────────

    def _scan_iam(self, session: boto3.session.Session) -> None:
        # Use us-east-1 since IAM is a global service
        client = session.client("iam", config=self._client_config)
        paginator = client.get_paginator("list_roles")
        count = 0
        for page in paginator.paginate(MaxItems=200):
            self._ensure_not_cancelled()
            self._increment_api_call("iam", "list_roles")
            for role in page.get("Roles", []):
                self._ensure_not_cancelled()
                arn = role.get("Arn", "")
                node_id = self._add_arn_node(arn, label=role.get("RoleName"), node_type="role")
                self._node(node_id, service="iam", created=str(role.get("CreateDate", "")))
                count += 1
            if count >= 200:
                self.store.add_warning("IAM: showing first 200 roles only.")
                return

    # ── Cognito ──────────────────────────────────────────────────────────────

    _COGNITO_LAMBDA_TRIGGERS = [
        "PreSignUp", "CustomMessage", "PostConfirmation", "PreAuthentication",
        "PostAuthentication", "DefineAuthChallenge", "CreateAuthChallenge",
        "VerifyAuthChallengeResponse", "PreTokenGeneration", "UserMigration",
        "CustomSMSSender", "CustomEmailSender",
    ]

    def _scan_cognito(self, session: boto3.session.Session) -> None:
        client = self._client(session, "cognito-idp")
        pool_nodes: List[tuple[str, str]] = []  # (pool_id, node_id)
        next_token: Optional[str] = None

        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {"MaxResults": 60}
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("cognito", "list_user_pools")
            page = client.list_user_pools(**kwargs)
            for pool in page.get("UserPools", []):
                self._ensure_not_cancelled()
                pool_id = pool.get("Id", "")
                arn = f"arn:aws:cognito-idp:{self._region}:{self._account_id}:userpool/{pool_id}"
                node_id = self._make_node_id("cognito", pool_id)
                self._node(node_id, label=pool.get("Name", pool_id), service="cognito", type="user_pool", arn=arn)
                pool_nodes.append((pool_id, node_id))
            next_token = page.get("NextToken")
            if not next_token:
                break

        # Cognito → Lambda (pre/post hooks)
        if pool_nodes:
            workers = max(1, min(8, len(pool_nodes)))
            with ThreadPoolExecutor(max_workers=workers) as pool_executor:
                futures = {
                    pool_executor.submit(self._fetch_cognito_lambda_config, client, pool_id): node_id
                    for pool_id, node_id in pool_nodes
                }
                self._drain_futures(futures, self._apply_cognito_lambda_edges)

    def _fetch_cognito_lambda_config(self, client: Any, pool_id: str) -> Dict[str, Any]:
        try:
            self._increment_api_call("cognito", "describe_user_pool")
            return client.describe_user_pool(UserPoolId=pool_id).get("UserPool", {}).get("LambdaConfig", {})
        except (ClientError, BotoCoreError) as exc:
            logger.debug("Cognito describe_user_pool failed for %s: %s", pool_id, exc)
            return {}

    def _apply_cognito_lambda_edges(self, future: Future[Any], pool_node: str) -> None:
        try:
            lambda_config = future.result()
        except Exception:
            return
        self._ensure_not_cancelled()
        for trigger in self._COGNITO_LAMBDA_TRIGGERS:
            fn_arn = lambda_config.get(trigger, "")
            if fn_arn and fn_arn.startswith("arn:aws:lambda:"):
                target_node = self._add_arn_node(fn_arn)
                self.store.add_edge(
                    pool_node, target_node, relationship="triggers", via=f"cognito_{trigger.lower()}"
                )

    # ── CloudFront ───────────────────────────────────────────────────────────

    def _scan_cloudfront(self, session: boto3.session.Session) -> None:
        # CloudFront is a global service — always query us-east-1
        client = session.client("cloudfront", config=self._client_config)
        paginator = client.get_paginator("list_distributions")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("cloudfront", "list_distributions")
            dist_list = page.get("DistributionList", {})
            for dist in dist_list.get("Items", []):
                self._ensure_not_cancelled()
                arn = dist.get("ARN", "")
                domain = dist.get("DomainName", "")
                node_id = self._add_arn_node(arn, label=domain or dist.get("Id"), node_type="distribution")
                self._node(
                    node_id,
                    service="cloudfront",
                    state=dist.get("Status"),
                    domain=domain,
                )
                # CloudFront → S3 / API Gateway / ALB origins
                for origin in (dist.get("Origins") or {}).get("Items", []):
                    origin_domain = origin.get("DomainName", "")
                    # S3 origins: bucket.s3.amazonaws.com or bucket.s3.region.amazonaws.com
                    if ".s3." in origin_domain or origin_domain.endswith(".s3.amazonaws.com"):
                        bucket_name = origin_domain.split(".s3.")[0]
                        s3_node = self._make_node_id("s3", bucket_name)
                        self._node(
                            s3_node,
                            label=bucket_name,
                            service="s3",
                            type="bucket",
                            arn=f"arn:aws:s3:::{bucket_name}",
                        )
                        self.store.add_edge(
                            node_id, s3_node, relationship="serves_from", via="cloudfront_origin"
                        )
                    elif "execute-api" in origin_domain:
                        # API Gateway origin
                        api_id = origin_domain.split(".execute-api.")[0] if ".execute-api." in origin_domain else origin_domain
                        apigw_node = self._make_node_id("apigateway", api_id)
                        self._node(apigw_node, label=api_id, service="apigateway", type="api")
                        self.store.add_edge(node_id, apigw_node, relationship="serves_from",
                                            via="cloudfront_origin")
                    elif ".elb.amazonaws.com" in origin_domain or ".elasticloadbalancing." in origin_domain:
                        # ALB/ELB origin
                        elb_node = self._make_node_id("elb", origin_domain)
                        self._node(elb_node, label=origin_domain, service="elb", type="load_balancer")
                        self.store.add_edge(node_id, elb_node, relationship="serves_from",
                                            via="cloudfront_origin")

                # Phase 2, Item 7: CloudFront → Lambda@Edge (once per distribution)
                self._extract_cloudfront_lambda_edges(node_id, dist)

    def _extract_cloudfront_lambda_edges(self, cf_node: str, dist: Dict[str, Any]) -> None:
        """Extract Lambda@Edge associations from CloudFront cache behaviors."""
        behaviors: List[Dict[str, Any]] = []
        default_behavior = dist.get("DefaultCacheBehavior")
        if default_behavior:
            behaviors.append(default_behavior)
        for behavior in (dist.get("CacheBehaviors") or {}).get("Items", []):
            behaviors.append(behavior)

        seen_arns: Set[str] = set()
        for behavior in behaviors:
            for assoc in (behavior.get("LambdaFunctionAssociations") or {}).get("Items", []):
                fn_arn = assoc.get("LambdaFunctionARN", "")
                if not fn_arn or not fn_arn.startswith("arn:aws:lambda:") or fn_arn in seen_arns:
                    continue
                seen_arns.add(fn_arn)
                base_arn = self._base_lambda_arn(fn_arn)
                target = self._add_arn_node(base_arn, node_type="lambda")
                self.store.add_edge(cf_node, target, relationship="invokes",
                                    via="cloudfront_lambda_edge",
                                    event_type=assoc.get("EventType"))

    # ── ElastiCache ──────────────────────────────────────────────────────────

    def _scan_elasticache(self, session: boto3.session.Session) -> None:
        client = self._client(session, "elasticache")
        paginator = client.get_paginator("describe_cache_clusters")
        for page in paginator.paginate():
            self._ensure_not_cancelled()
            self._increment_api_call("elasticache", "describe_cache_clusters")
            for cluster in page.get("CacheClusters", []):
                self._ensure_not_cancelled()
                arn = cluster.get("ARN", "")
                cluster_id = cluster.get("CacheClusterId", "")
                node_id = self._add_arn_node(arn, label=cluster_id, node_type="cluster") if arn else self._make_node_id("elasticache", cluster_id)
                if not arn:
                    arn = f"arn:aws:elasticache:{self._region}:{self._account_id}:cluster/{cluster_id}"
                    self._node(node_id, label=cluster_id, service="elasticache", type="cluster", arn=arn)
                self._node(
                    node_id,
                    service="elasticache",
                    engine=cluster.get("Engine"),
                    engine_version=cluster.get("EngineVersion"),
                    node_type=cluster.get("CacheNodeType"),
                    state=cluster.get("CacheClusterStatus"),
                )
                # ElastiCache VPC topology edges
                for ec_sg in cluster.get("SecurityGroups", []):
                    ec_sg_id = ec_sg.get("SecurityGroupId")
                    if ec_sg_id:
                        ec_sg_node = self._make_node_id("vpc", f"sg/{ec_sg_id}")
                        self._node(ec_sg_node, label=ec_sg_id, service="vpc", type="security_group")
                        self.store.add_edge(ec_sg_node, node_id, relationship="protects", via="elasticache_security_group")

    # ── Glue ─────────────────────────────────────────────────────────────────

    def _scan_glue(self, session: boto3.session.Session) -> None:
        client = self._client(session, "glue")
        job_names: List[str] = []

        # Jobs
        next_token: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["NextToken"] = next_token
            self._increment_api_call("glue", "list_jobs")
            page = client.list_jobs(**kwargs)
            for job_name in page.get("JobNames", []):
                self._ensure_not_cancelled()
                arn = f"arn:aws:glue:{self._region}:{self._account_id}:job/{job_name}"
                node_id = self._make_node_id("glue", job_name)
                self._node(node_id, label=job_name, service="glue", type="job", arn=arn)
                job_names.append(job_name)
            next_token = page.get("NextToken")
            if not next_token:
                break

        # Glue → S3 (source/target buckets from job arguments) and Glue → RDS (connections)
        if job_names:
            workers = max(1, min(8, len(job_names)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._fetch_glue_job_detail, client, name): name
                    for name in job_names
                }
                self._drain_futures(futures, self._apply_glue_job_edges)

        # Phase 3, Item 11: Glue crawlers and triggers
        self._scan_glue_crawlers(client)
        self._scan_glue_triggers(client)

    def _fetch_glue_job_detail(self, client: Any, job_name: str) -> Dict[str, Any]:
        try:
            self._increment_api_call("glue", "get_job")
            return client.get_job(JobName=job_name).get("Job", {})
        except (ClientError, BotoCoreError) as exc:
            logger.debug("Glue get_job failed for %s: %s", job_name, exc)
            return {}

    def _apply_glue_job_edges(self, future: Future[Any], job_name: str) -> None:
        try:
            job = future.result()
        except Exception:
            return
        self._ensure_not_cancelled()
        job_node = self._make_node_id("glue", job_name)

        # Extract S3 bucket references from job arguments
        args = job.get("DefaultArguments") or {}
        s3_buckets: Set[str] = set()
        for val in args.values():
            if isinstance(val, str) and val.startswith("s3://"):
                # s3://bucket-name/path/... → extract bucket-name
                parts = val[5:].split("/")
                if parts[0]:
                    s3_buckets.add(parts[0])
        for bucket_name in s3_buckets:
            s3_node = self._make_node_id("s3", bucket_name)
            self._node(s3_node, label=bucket_name, service="s3", type="bucket",
                        arn=f"arn:aws:s3:::{bucket_name}")
            self.store.add_edge(job_node, s3_node, relationship="reads_writes", via="glue_job_argument")

        # Glue connections (JDBC → RDS/Redshift)
        for conn_name in _safe_list(job.get("Connections", {}).get("Connections")):
            conn_node = self._make_node_id("glue", f"connection:{conn_name}")
            self._node(conn_node, label=conn_name, service="glue", type="connection",
                        arn=f"arn:aws:glue:{self._region}:{self._account_id}:connection/{conn_name}")
            self.store.add_edge(job_node, conn_node, relationship="uses", via="glue_connection")

    def _scan_glue_crawlers(self, client: Any) -> None:
        """Discover Glue crawlers and their S3/DynamoDB/database targets (Phase 3, Item 11)."""
        next_token: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["NextToken"] = next_token
            try:
                self._increment_api_call("glue", "get_crawlers")
                page = client.get_crawlers(**kwargs)
            except (ClientError, BotoCoreError) as exc:
                logger.debug("Glue get_crawlers failed: %s", exc)
                self.store.add_warning(f"glue crawler scan failed: {type(exc).__name__}")
                return

            for crawler in page.get("Crawlers", []):
                self._ensure_not_cancelled()
                name = crawler.get("Name", "")
                if not name:
                    continue
                arn = f"arn:aws:glue:{self._region}:{self._account_id}:crawler/{name}"
                node_id = self._make_node_id("glue", f"crawler:{name}")
                self._node(node_id, label=name, service="glue", type="crawler", arn=arn,
                           state=crawler.get("State"))

                # Crawler → S3 targets
                for target in (crawler.get("Targets") or {}).get("S3Targets", []):
                    path = target.get("Path", "")
                    if path.startswith("s3://"):
                        bucket = path[5:].split("/")[0]
                        if bucket:
                            s3_node = self._make_node_id("s3", bucket)
                            self._node(s3_node, label=bucket, service="s3", type="bucket",
                                       arn=f"arn:aws:s3:::{bucket}")
                            self.store.add_edge(node_id, s3_node, relationship="crawls",
                                                via="glue_crawler_target")

                # Crawler → DynamoDB targets
                for target in (crawler.get("Targets") or {}).get("DynamoDBTargets", []):
                    table = target.get("Path", "")
                    if table:
                        ddb_node = self._make_node_id("dynamodb", table)
                        self._node(ddb_node, label=table, service="dynamodb", type="table")
                        self.store.add_edge(node_id, ddb_node, relationship="crawls",
                                            via="glue_crawler_target")

                # Crawler → output database
                db_name = crawler.get("DatabaseName", "")
                if db_name:
                    db_node = self._make_node_id("glue", f"database:{db_name}")
                    self._node(db_node, label=db_name, service="glue", type="database",
                               arn=f"arn:aws:glue:{self._region}:{self._account_id}:database/{db_name}")
                    self.store.add_edge(node_id, db_node, relationship="populates",
                                        via="glue_crawler_output")

            next_token = page.get("NextToken")
            if not next_token:
                break

    def _scan_glue_triggers(self, client: Any) -> None:
        """Discover Glue triggers and their job/crawler action edges (Phase 3, Item 11)."""
        next_token: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["NextToken"] = next_token
            try:
                self._increment_api_call("glue", "get_triggers")
                page = client.get_triggers(**kwargs)
            except (ClientError, BotoCoreError) as exc:
                logger.debug("Glue get_triggers failed: %s", exc)
                self.store.add_warning(f"glue trigger scan failed: {type(exc).__name__}")
                return

            for trigger in page.get("Triggers", []):
                self._ensure_not_cancelled()
                name = trigger.get("Name", "")
                if not name:
                    continue
                node_id = self._make_node_id("glue", f"trigger:{name}")
                self._node(node_id, label=name, service="glue", type="trigger",
                           arn=f"arn:aws:glue:{self._region}:{self._account_id}:trigger/{name}",
                           trigger_type=trigger.get("Type"), state=trigger.get("State"))

                # Trigger → job/crawler actions
                for action in trigger.get("Actions", []):
                    job_name = action.get("JobName", "")
                    if job_name:
                        job_node = self._make_node_id("glue", job_name)
                        self.store.add_edge(node_id, job_node, relationship="triggers",
                                            via="glue_trigger")
                    crawler_name = action.get("CrawlerName", "")
                    if crawler_name:
                        crawler_node = self._make_node_id("glue", f"crawler:{crawler_name}")
                        self.store.add_edge(node_id, crawler_node, relationship="triggers",
                                            via="glue_trigger")

                # Predicate conditions: job completion → trigger
                for condition in (trigger.get("Predicate") or {}).get("Conditions", []):
                    pred_job = condition.get("JobName", "")
                    if pred_job:
                        pred_node = self._make_node_id("glue", pred_job)
                        self.store.add_edge(pred_node, node_id, relationship="triggers",
                                            via="glue_trigger_predicate")

            next_token = page.get("NextToken")
            if not next_token:
                break

    # ── AppSync ──────────────────────────────────────────────────────────────

    def _scan_appsync(self, session: boto3.session.Session) -> None:
        client = self._client(session, "appsync")
        api_ids: List[tuple[str, str]] = []  # (api_id, node_id)
        next_token: Optional[str] = None

        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {}
            if next_token:
                kwargs["nextToken"] = next_token
            self._increment_api_call("appsync", "list_graphql_apis")
            page = client.list_graphql_apis(**kwargs)
            for api in page.get("graphqlApis", []):
                self._ensure_not_cancelled()
                arn = api.get("arn", "")
                api_id = api.get("apiId", "")
                node_id = self._add_arn_node(arn, label=api.get("name"), node_type="api")
                self._node(node_id, service="appsync", auth_type=api.get("authenticationType"))
                api_ids.append((api_id, node_id))
            next_token = page.get("nextToken")
            if not next_token:
                break

        # AppSync → Lambda / DynamoDB / RDS (data sources)
        if api_ids:
            workers = max(1, min(8, len(api_ids)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._fetch_appsync_data_sources, client, api_id): node_id
                    for api_id, node_id in api_ids
                }
                self._drain_futures(futures, self._apply_appsync_edges)

    def _fetch_appsync_data_sources(self, client: Any, api_id: str) -> List[Dict[str, Any]]:
        sources: List[Dict[str, Any]] = []
        next_token: Optional[str] = None
        while True:
            try:
                self._ensure_not_cancelled()
                kwargs: Dict[str, Any] = {"apiId": api_id}
                if next_token:
                    kwargs["nextToken"] = next_token
                self._increment_api_call("appsync", "list_data_sources")
                page = client.list_data_sources(**kwargs)
                sources.extend(page.get("dataSources", []))
                next_token = page.get("nextToken")
                if not next_token:
                    break
            except (ClientError, BotoCoreError) as exc:
                logger.debug("AppSync list_data_sources failed for %s: %s", api_id, exc)
                break
        return sources

    def _apply_appsync_edges(self, future: Future[Any], api_node: str) -> None:
        try:
            sources = future.result()
        except Exception:
            return
        self._ensure_not_cancelled()
        for source in sources:
            src_type = source.get("type", "")
            if src_type == "AWS_LAMBDA":
                fn_arn = (source.get("lambdaConfig") or {}).get("lambdaFunctionArn", "")
                if fn_arn.startswith("arn:aws:lambda:"):
                    target = self._add_arn_node(fn_arn)
                    self.store.add_edge(api_node, target, relationship="resolves_via", via="appsync_datasource")
            elif src_type == "AMAZON_DYNAMODB":
                table_name = (source.get("dynamodbConfig") or {}).get("tableName", "")
                if table_name:
                    node_id = self._make_node_id("dynamodb", table_name)
                    self._node(node_id, label=table_name, service="dynamodb", type="table", arn=table_name)
                    self.store.add_edge(api_node, node_id, relationship="resolves_via", via="appsync_datasource")
            elif src_type == "RELATIONAL_DATABASE":
                db_cluster_id = (source.get("relationalDatabaseConfig") or {}).get(
                    "rdsHttpEndpointConfig", {}
                ).get("dbClusterIdentifier", "")
                if db_cluster_id:
                    node_id = self._make_node_id("rds", db_cluster_id)
                    self._node(node_id, label=db_cluster_id, service="rds", type="cluster", arn=db_cluster_id)
                    self.store.add_edge(api_node, node_id, relationship="resolves_via", via="appsync_datasource")

    # ── Route 53 ─────────────────────────────────────────────────────────────

    # Map Route 53 canonical hosted zone IDs to AWS service types for alias target detection
    _R53_ALIAS_ZONE_TO_SERVICE: Dict[str, str] = {
        "Z2FDTNDATAQYW2": "cloudfront",   # CloudFront global
        "Z35SXDOTRQ7X7K": "elb",          # us-east-1 ELB
        "Z368ELLRRE2KJ0": "elb",          # us-west-2 ELB
        "Z3DZXE0Q79N41H": "elb",          # us-west-1 ELB
        "Z1H1FL5HABSF5":  "elb",          # ap-southeast-1 ELB
        "Z3QFB96KE08076": "elb",          # ap-southeast-2 ELB
        "Z3AADJGX6KTTL2": "elb",          # ap-northeast-1 ELB
        "Z215JYRZR1TBD5": "elb",          # eu-west-1 ELB
    }

    def _scan_route53(self, session: boto3.session.Session) -> None:
        # Route 53 is global — use us-east-1
        client = session.client("route53", config=self._client_config)
        zone_nodes: List[tuple[str, str]] = []  # (zone_id, node_id)

        # Hosted zones
        marker: Optional[str] = None
        while True:
            self._ensure_not_cancelled()
            kwargs: Dict[str, Any] = {"MaxItems": "100"}
            if marker:
                kwargs["Marker"] = marker
            self._increment_api_call("route53", "list_hosted_zones")
            page = client.list_hosted_zones(**kwargs)
            for zone in page.get("HostedZones", []):
                self._ensure_not_cancelled()
                zone_id = zone["Id"].split("/")[-1]
                zone_name = zone.get("Name", zone_id).rstrip(".")
                arn = f"arn:aws:route53:::hostedzone/{zone_id}"
                node_id = self._make_node_id("route53", zone_id)
                self._node(
                    node_id,
                    label=zone_name,
                    service="route53",
                    type="hosted_zone",
                    arn=arn,
                    private_zone=zone.get("Config", {}).get("PrivateZone", False),
                    record_count=zone.get("ResourceRecordSetCount"),
                )
                zone_nodes.append((zone_id, node_id))
            if not page.get("IsTruncated"):
                break
            marker = page.get("NextMarker")

        # Route 53 → CloudFront / ELB (alias records)
        if zone_nodes:
            workers = max(1, min(8, len(zone_nodes)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._fetch_r53_alias_targets, client, zone_id): zone_node
                    for zone_id, zone_node in zone_nodes
                }
                self._drain_futures(futures, self._apply_r53_edges)

    def _fetch_r53_alias_targets(self, client: Any, zone_id: str) -> List[Dict[str, Any]]:
        aliases: List[Dict[str, Any]] = []
        next_id: Optional[str] = None
        next_type: Optional[str] = None
        while True:
            try:
                self._ensure_not_cancelled()
                kwargs: Dict[str, Any] = {"HostedZoneId": zone_id, "MaxItems": "300"}
                if next_id:
                    kwargs["StartRecordName"] = next_id
                    kwargs["StartRecordType"] = next_type
                self._increment_api_call("route53", "list_resource_record_sets")
                page = client.list_resource_record_sets(**kwargs)
                for record in page.get("ResourceRecordSets", []):
                    alias = record.get("AliasTarget")
                    if alias:
                        aliases.append({
                            "name": record.get("Name", "").rstrip("."),
                            "dns": alias.get("DNSName", "").rstrip("."),
                            "zone": alias.get("HostedZoneId", ""),
                        })
                if not page.get("IsTruncated"):
                    break
                next_id = page.get("NextRecordName")
                next_type = page.get("NextRecordType")
            except (ClientError, BotoCoreError) as exc:
                logger.debug("Route53 list_resource_record_sets failed for %s: %s", zone_id, exc)
                break
        return aliases

    def _apply_r53_edges(self, future: Future[Any], zone_node: str) -> None:
        try:
            aliases = future.result()
        except Exception:
            return
        self._ensure_not_cancelled()
        for alias in aliases:
            target_svc = self._R53_ALIAS_ZONE_TO_SERVICE.get(alias["zone"])
            dns = alias["dns"]
            if target_svc == "cloudfront" and ".cloudfront.net" in dns:
                # Look up existing CloudFront node by domain, or create a phantom
                existing = self._find_node_by_attr("cloudfront", "domain", dns)
                if existing:
                    cf_node = existing
                else:
                    cf_node = self._make_node_id("cloudfront", dns)
                    self._node(cf_node, label=dns, service="cloudfront", type="distribution", domain=dns, phantom=True)
                self.store.add_edge(zone_node, cf_node, relationship="routes_to", via="route53_alias")
            elif "execute-api" in dns:
                # Phase 3, Item 9: Route53 → API Gateway
                api_id = dns.split(".execute-api.")[0] if ".execute-api." in dns else dns
                apigw_node = self._make_node_id("apigateway", api_id)
                self._node(apigw_node, label=api_id, service="apigateway", type="api")
                self.store.add_edge(zone_node, apigw_node, relationship="routes_to", via="route53_alias")
            elif ".s3-website" in dns or dns.endswith(".s3.amazonaws.com"):
                # Phase 3, Item 9: Route53 → S3 website
                bucket_name = dns.split(".s3")[0]
                if bucket_name:
                    s3_node = self._make_node_id("s3", bucket_name)
                    self._node(s3_node, label=bucket_name, service="s3", type="bucket",
                               arn=f"arn:aws:s3:::{bucket_name}")
                    self.store.add_edge(zone_node, s3_node, relationship="routes_to", via="route53_alias")
            elif target_svc == "elb":
                # Look up existing ELB node by domain, or create a phantom
                existing = self._find_node_by_attr("elb", "label", dns)
                if existing:
                    elb_node = existing
                else:
                    elb_node = self._make_node_id("elb", dns)
                    self._node(elb_node, label=dns, service="elb", type="load_balancer", phantom=True)
                self.store.add_edge(zone_node, elb_node, relationship="routes_to", via="route53_alias")

    # ── Redshift ──────────────────────────────────────────────────────────────

    def _scan_redshift(self, session: boto3.session.Session) -> None:
        client = self._client(session, "redshift")
        try:
            paginator = client.get_paginator("describe_clusters")
            for page in paginator.paginate():
                self._ensure_not_cancelled()
                self._increment_api_call("redshift", "describe_clusters")
                for cluster in page.get("Clusters", []):
                    self._ensure_not_cancelled()
                    cluster_id = cluster.get("ClusterIdentifier", "")
                    arn = f"arn:aws:redshift:{self._region}:{self._account_id}:cluster:{cluster_id}"
                    node_id = self._make_node_id("redshift", cluster_id)
                    self._node(
                        node_id,
                        label=cluster_id,
                        service="redshift",
                        type="cluster",
                        arn=arn,
                        state=cluster.get("ClusterStatus"),
                        node_type=cluster.get("NodeType"),
                        num_nodes=cluster.get("NumberOfNodes"),
                        db_name=cluster.get("DBName"),
                        vpc_id=cluster.get("VpcId"),
                    )
                    # Redshift VPC topology edges
                    rs_vpc_id = cluster.get("VpcId")
                    if rs_vpc_id:
                        rs_vpc_node = self._make_node_id("vpc", f"vpc/{rs_vpc_id}")
                        self._node(rs_vpc_node, label=rs_vpc_id, service="vpc", type="vpc")
                        self.store.add_edge(rs_vpc_node, node_id, relationship="contains", via="redshift_vpc_membership")
                    for vsg in cluster.get("VpcSecurityGroups", []):
                        rs_sg_id = vsg.get("VpcSecurityGroupId")
                        if rs_sg_id:
                            rs_sg_node = self._make_node_id("vpc", f"sg/{rs_sg_id}")
                            self._node(rs_sg_node, label=rs_sg_id, service="vpc", type="security_group")
                            self.store.add_edge(rs_sg_node, node_id, relationship="protects", via="redshift_security_group")
        except (ClientError, BotoCoreError) as exc:
            logger.warning("Redshift scan failed: %s", exc)

    def _scan_generic_service(self, session: boto3.session.Session, service_name: str) -> None:
        client = self._client(session, "resourcegroupstaggingapi")
        paginator = client.get_paginator("get_resources")

        discovered = 0
        try:
            page_iterator = paginator.paginate(ResourcesPerPage=100, ResourceTypeFilters=[service_name])
            for page in page_iterator:
                self._ensure_not_cancelled()
                self._increment_api_call("resourcegroupstaggingapi", "get_resources")
                for entry in page.get("ResourceTagMappingList", []):
                    self._ensure_not_cancelled()
                    arn = entry.get("ResourceARN")
                    if not arn:
                        continue
                    discovered += 1
                    node_id = self._add_arn_node(arn)
                    tags = {item.get("Key"): item.get("Value") for item in entry.get("Tags", [])}
                    self._node(node_id, service=service_name, tags=tags)
        except (ClientError, BotoCoreError) as exc:
            logger.warning("Generic service scan failed for %s: %s", service_name, exc)
            discovered = 0

        if discovered == 0:
            self.store.add_warning(f"{service_name} scanner is not specialized yet; no resources discovered.")
        else:
            self.store.add_warning(f"{service_name}: generic scan found {discovered} resource(s) but relationship discovery is not available for this service.")
