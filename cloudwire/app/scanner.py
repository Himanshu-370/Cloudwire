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
from .scanners.apigateway import ApiGatewayScannerMixin
from .scanners.appsync import AppSyncScannerMixin
from .scanners.cloudfront import CloudFrontScannerMixin
from .scanners.cognito import CognitoScannerMixin
from .scanners.dynamodb import DynamoDBScannerMixin
from .scanners.ec2 import EC2ScannerMixin
from .scanners.ecs import ECSScannerMixin
from .scanners.elasticache import ElastiCacheScannerMixin
from .scanners.eventbridge import EventBridgeScannerMixin
from .scanners.glue import GlueScannerMixin
from .scanners.iam import IAMScannerMixin
from .scanners.kinesis import KinesisScannerMixin
from .scanners.lambda_ import LambdaScannerMixin
from .scanners.rds import RDSScannerMixin
from .scanners.redshift import RedshiftScannerMixin
from .scanners.route53 import Route53ScannerMixin
from .scanners.s3 import S3ScannerMixin
from .scanners.sns import SNSScannerMixin
from .scanners.sqs import SqsScannerMixin
from .scanners.stepfunctions import StepFunctionsScannerMixin
from .scanners.vpc import VPCScannerMixin


def _sanitize_exc(exc: Exception) -> str:
    """Return a safe error summary without leaking AWS account IDs, ARNs, or role names."""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        return f"AWS API error ({code})" if code else "AWS API error"
    return type(exc).__name__


from .scanners._utils import _safe_list


_ARN_PATTERN = re.compile(r"^arn:aws:[a-z0-9-]+:")


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


class AWSGraphScanner(
    ApiGatewayScannerMixin,
    LambdaScannerMixin,
    SqsScannerMixin,
    EventBridgeScannerMixin,
    DynamoDBScannerMixin,
    EC2ScannerMixin,
    ECSScannerMixin,
    S3ScannerMixin,
    RDSScannerMixin,
    StepFunctionsScannerMixin,
    SNSScannerMixin,
    KinesisScannerMixin,
    IAMScannerMixin,
    CognitoScannerMixin,
    CloudFrontScannerMixin,
    ElastiCacheScannerMixin,
    GlueScannerMixin,
    AppSyncScannerMixin,
    Route53ScannerMixin,
    RedshiftScannerMixin,
    VPCScannerMixin,
):
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
        self._node_attr_lock = Lock()
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

    # ------------------------------------------------------------------
    # Scan orchestration
    # ------------------------------------------------------------------

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
                    self.store.add_warning(f"{service} scan failed: {_sanitize_exc(exc)}")
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
                    self.store.add_warning(f"vpc scan failed: {_sanitize_exc(exc)}")
            except (BotoCoreError, Exception) as exc:
                logger.exception("Error in VPC phase-2 scan")
                self.store.add_warning(f"vpc scan failed: {_sanitize_exc(exc)}")
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
                self.store.add_warning(f"{service} scan failed: {_sanitize_exc(exc)}")
        except BotoCoreError as exc:
            logger.warning("AWS API error scanning %s: %s", service, exc)
            self.store.add_warning(f"{service} scan failed: {_sanitize_exc(exc)}")
        except Exception as exc:
            logger.exception("Unexpected error scanning service %s", service)
            self.store.add_warning(f"{service} scan failed: {_sanitize_exc(exc)}")
        return int((perf_counter() - start) * 1000)

    # ------------------------------------------------------------------
    # Shared helpers (used by service scanner mixins via self)
    # ------------------------------------------------------------------

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
            with self._node_attr_lock:
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
        raw = parts[2] if len(parts) > 2 else "unknown"
        return _normalize_service_name(raw)

    def _make_node_id(self, service: str, resource: str) -> str:
        return f"{service}:{resource}"

    def _find_node_by_attr(self, service: str, attr: str, value: str) -> Optional[str]:
        """Find an existing node of a given service where attr == value. Returns node_id or None."""
        with self._node_attr_lock:
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

            for node_id, attrs in self.store.iter_nodes_by_service(service_name):
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
        for node_id, attrs in self.store.iter_nodes_by_service("vpc"):
            if attrs.get("type") == "vpc":
                # node_id format: "vpc:vpc/{vpc_id}"
                parts = node_id.split("vpc/", 1)
                if len(parts) == 2:
                    vpc_ids.add(parts[1])
        return vpc_ids

    def _compute_network_exposure(self) -> None:
        """Compute exposed_internet flag by tracing IGW -> route table -> subnet -> SG -> resource paths.

        Runs after all scanners complete — no concurrent graph mutations expected.
        Snapshots the graph under the lock, traverses the snapshot, then applies results.
        """
        # Snapshot graph data to avoid holding the lock during traversal
        graph = self.store.snapshot_graph()

        # Find all IGW nodes
        igw_nodes = [n for n, d in graph.nodes(data=True) if d.get("type") == "internet_gateway"]
        if not igw_nodes:
            return

        # Build subnet -> resources that are "contained" in that subnet
        subnet_resources: Dict[str, List[str]] = {}
        # Build resource -> protecting SGs
        resource_sgs: Dict[str, List[str]] = {}

        for src, tgt, attrs in graph.edges(data=True):
            rel = attrs.get("relationship", "")
            if rel == "contains":
                src_type = graph.nodes[src].get("type", "")
                if src_type == "subnet":
                    subnet_resources.setdefault(src, []).append(tgt)
            elif rel == "protects":
                resource_sgs.setdefault(tgt, []).append(src)

        # Build IGW -> Internet node mapping (for path node IDs)
        igw_to_internet: Dict[str, str] = {}
        for src, tgt, attrs in graph.edges(data=True):
            if attrs.get("relationship") == "gateway":
                igw_to_internet[tgt] = src  # internet_node -> igw_node, so tgt=igw, src=internet

        # For each IGW, find route tables it feeds into, then subnets those RTBs route to
        # Store both path string and node IDs for frontend highlighting
        internet_reachable_subnets: Dict[str, tuple] = {}  # subnet_node -> (path_string, [node_ids])

        for igw_node in igw_nodes:
            igw_label = graph.nodes[igw_node].get("label", igw_node)
            internet_node = igw_to_internet.get(igw_node)
            # IGW -> routes_via -> RTB (direct edge from _scan_vpc)
            for _, rtb_node, e_attrs in graph.out_edges(igw_node, data=True):
                if e_attrs.get("relationship") != "routes_via":
                    continue
                rtb_label = graph.nodes[rtb_node].get("label", rtb_node)
                # RTB -> routes -> subnet
                for _, subnet_node, r_attrs in graph.out_edges(rtb_node, data=True):
                    if r_attrs.get("relationship") == "routes":
                        path_str = f"{igw_label} \u2192 {rtb_label}"
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
                        path = f"{path_prefix} \u2192 {subnet_label} \u2192 {open_sg}"
                        path_node_ids = path_nodes_prefix + [sg_node, resource_node]
                        exposure_updates.append((resource_node, path, path_node_ids))
                        break

        # Apply results via public API
        if exposure_updates:
            self.store.batch_update_nodes([
                (resource_node, {
                    "exposed_internet": True,
                    "internet_path": path,
                    "internet_path_nodes": path_node_ids,
                })
                for resource_node, path, path_node_ids in exposure_updates
            ])

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

    # ------------------------------------------------------------------
    # Generic (fallback) scanner — stays here since it's used by the orchestrator
    # ------------------------------------------------------------------

    # Map canonical service names to the AWS Resource Groups Tagging API
    # service prefixes.  The tagging API uses its own namespace which
    # doesn't always match the boto3 / ARN service slug.
    _TAGGING_API_SERVICE_MAP: Dict[str, str] = {
        "emr": "elasticmapreduce",
        "stepfunctions": "states",
        "opensearch": "es",                    # tagging API still uses old "es" prefix
        "elb": "elasticloadbalancing",
        "cognito": "cognito-idp",
        "secretsmanager": "secretsmanager",
        "cloudwatch": "monitoring",
        "efs": "elasticfilesystem",
        "elasticbeanstalk": "elasticbeanstalk",
        "acm": "acm",
        "mq": "amazonmq",
        "kafka": "kafka",                      # MSK uses "kafka" in tagging API
        "wafv2": "wafv2",
        "guardduty": "guardduty",
        "codepipeline": "codepipeline",
        "codebuild": "codebuild",
    }

    def _scan_generic_service(self, session: boto3.session.Session, service_name: str) -> None:
        """Fallback scanner: discovers resources via the tagging API and infers
        parent/child relationships from ARN structure.

        For example, ``arn:aws:emr:us-east-1:123:cluster/j-ABC`` and
        ``arn:aws:emr:us-east-1:123:cluster/j-ABC/step/s-DEF`` will be linked
        as parent → child.
        """
        client = self._client(session, "resourcegroupstaggingapi")
        paginator = client.get_paginator("get_resources")

        # node_id keyed by ARN, resource_suffix keyed by ARN (for parent matching)
        discovered_arns: List[str] = []
        node_ids: Dict[str, str] = {}

        # Resolve the correct tagging API service prefix
        tagging_service = self._TAGGING_API_SERVICE_MAP.get(service_name, service_name)

        try:
            page_iterator = paginator.paginate(ResourcesPerPage=100, ResourceTypeFilters=[tagging_service])
            for page in page_iterator:
                self._ensure_not_cancelled()
                self._increment_api_call("resourcegroupstaggingapi", "get_resources")
                for entry in page.get("ResourceTagMappingList", []):
                    self._ensure_not_cancelled()
                    arn = entry.get("ResourceARN")
                    if not arn:
                        continue
                    discovered_arns.append(arn)
                    node_id = self._add_arn_node(arn)
                    node_ids[arn] = node_id
                    tags = {item.get("Key"): item.get("Value") for item in entry.get("Tags", [])}

                    # Extract resource type from ARN for richer labels
                    # ARN format: arn:aws:service:region:account:resource-type/id or resource-type:id
                    resource_part = arn.split(":", 5)[-1] if len(arn.split(":")) >= 6 else ""
                    resource_type = ""
                    if "/" in resource_part:
                        resource_type = resource_part.split("/")[0]
                    elif ":" in resource_part:
                        resource_type = resource_part.split(":")[0]

                    self._node(node_id, service=service_name, tags=tags,
                               type=resource_type or "resource")
        except (ClientError, BotoCoreError) as exc:
            logger.warning("Generic service scan failed for %s: %s", service_name, exc)
            discovered_arns = []

        # Infer parent→child edges from ARN hierarchy.
        # E.g., arn:...:cluster/j-ABC is parent of arn:...:cluster/j-ABC/step/s-DEF
        if len(discovered_arns) > 1:
            sorted_arns = sorted(discovered_arns)
            for i, arn in enumerate(sorted_arns):
                for j in range(i + 1, len(sorted_arns)):
                    candidate = sorted_arns[j]
                    if candidate.startswith(arn + "/") or candidate.startswith(arn + ":"):
                        # candidate is a sub-resource of arn
                        parent_id = node_ids.get(arn)
                        child_id = node_ids.get(candidate)
                        if parent_id and child_id:
                            self.store.add_edge(parent_id, child_id, relationship="CONTAINS")
                    elif not candidate.startswith(arn[:arn.rfind("/") + 1] if "/" in arn else arn[:arn.rfind(":") + 1]):
                        # No more potential children — different prefix
                        break

        discovered = len(discovered_arns)
        if discovered == 0:
            self.store.add_warning(f"{service_name}: no resources discovered via tagging API.")
        else:
            logger.info("%s: discovered %d resource(s) via tagging API (generic scan)", service_name, discovered)
