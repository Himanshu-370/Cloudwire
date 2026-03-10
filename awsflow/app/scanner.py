from __future__ import annotations

import logging
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .graph_store import GraphStore
from .models import ScanMode


def _normalize_service_name(service: str) -> str:
    key = service.lower().strip()
    aliases = {
        "api-gateway": "apigateway",
        "apigw": "apigateway",
        "event-bridge": "eventbridge",
        "events": "eventbridge",
    }
    return aliases.get(key, key)


def _safe_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


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


class ScanCancelledError(Exception):
    pass


class AWSGraphScanner:
    def __init__(self, store: GraphStore, *, options: ScanExecutionOptions) -> None:
        self.store = store
        self.options = options
        self._region: str = "unknown"
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
        }
        self._iam_role_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._iam_cache_lock = Lock()
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
        total_services = len(normalized_services)
        completed = 0
        started_at = perf_counter()

        workers = max(1, min(self.options.max_service_workers, total_services or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_service: Dict[Any, str] = {}
            for service in normalized_services:
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
                    return
                except Exception as exc:
                    logger.exception("Unhandled error draining future for service %s", service)
                    self.store.add_warning(f"{service} scan failed: {type(exc).__name__} - {exc}")
                completed += 1
                if progress_callback:
                    progress_callback("finish", service, completed, total_services)

            self._drain_futures(future_to_service, on_service_result)

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
            else:
                self._scan_generic_service(session, service)
        except ScanCancelledError:
            return int((perf_counter() - start) * 1000)
        except (ClientError, BotoCoreError) as exc:
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

    def _drain_futures(
        self,
        future_map: Dict[Future[Any], Any],
        on_result: Callable[[Future[Any], Any], None],
    ) -> None:
        pending = set(future_map)
        while pending:
            if self._is_cancelled():
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

    def _apply_apigwv2_integrations(self, future: Future[Any], api_node: str) -> None:
        integrations = future.result()
        self._ensure_not_cancelled()
        for integration in integrations:
            self._ensure_not_cancelled()
            lambda_arn = self._parse_lambda_arn(integration.get("IntegrationUri"))
            if not lambda_arn:
                continue
            lambda_node = self._add_arn_node(lambda_arn, node_type="lambda")
            self.store.add_edge(
                api_node,
                lambda_node,
                relationship="invokes",
                via="apigatewayv2_integration",
            )

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
                                self._fetch_apigw_rest_integration_lambda,
                                client,
                                rest_api_id,
                                resource_id,
                                http_method,
                            ): api_node
                            for rest_api_id, resource_id, http_method, api_node in tasks
                        }
                        self._drain_futures(futures, self._apply_apigateway_rest_integration)

            position = page.get("position")
            if not position:
                break

    def _fetch_apigw_rest_integration_lambda(
        self,
        client: Any,
        rest_api_id: str,
        resource_id: str,
        http_method: str,
    ) -> Optional[str]:
        try:
            self._increment_api_call("apigateway", "get_integration")
            integration = client.get_integration(
                restApiId=rest_api_id,
                resourceId=resource_id,
                httpMethod=http_method,
            )
        except ClientError as exc:
            logger.debug("Skipping API Gateway integration %s/%s/%s: %s", rest_api_id, resource_id, http_method, exc)
            return None
        return self._parse_lambda_arn(integration.get("uri"))

    def _apply_apigateway_rest_integration(self, future: Future[Any], api_node: str) -> None:
        lambda_arn = future.result()
        if not lambda_arn:
            return
        self._ensure_not_cancelled()
        lambda_node = self._add_arn_node(lambda_arn, node_type="lambda")
        self.store.add_edge(
            api_node,
            lambda_node,
            relationship="invokes",
            via="apigateway_rest_integration",
        )

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

        self._scan_lambda_event_sources_global(client, function_node_ids)
        if self.options.include_iam_inference:
            self._scan_lambda_iam_dependencies_parallel(session, role_to_function_nodes)

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
        except Exception as exc:
            logger.warning("IAM policy lookup failed for role %s: %s", role_name, exc)
            self.store.add_warning(f"iam policy lookup failed for {role_name}: {type(exc).__name__} - {exc}")
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
            if prefix in {"dynamodb", "sqs", "events", "lambda"}:
                normalized = "eventbridge" if prefix == "events" else prefix
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

        self._increment_api_call("iam", "list_role_policies")
        inline_policy_names = iam.list_role_policies(RoleName=role_name).get("PolicyNames", [])
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
            AttributeNames=["QueueArn", "VisibilityTimeout", "CreatedTimestamp"],
        ).get("Attributes", {})

    def _apply_sqs_queue_attributes(self, future: Future[Any], queue_url: str) -> None:
        attrs = future.result()
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
        targets = future.result()
        self._ensure_not_cancelled()
        rule_arn = rule.get("Arn") or f"rule:{rule.get('Name')}"
        rule_node = self._make_node_id(self._service_from_arn(rule_arn), rule_arn)
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
                    name_tag = next((t["Value"] for t in instance.get("Tags", []) if t.get("Key") == "Name"), None)
                    arn = f"arn:aws:ec2:{self._region}:{instance.get('OwnerId', '')}:instance/{instance_id}"
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
            node_id = self._add_arn_node(arn, label=cluster_name, node_type="cluster")
            self._node(node_id, service="ecs")

            # List services in this cluster
            svc_paginator = client.get_paginator("list_services")
            for svc_page in svc_paginator.paginate(cluster=arn):
                self._ensure_not_cancelled()
                self._increment_api_call("ecs", "list_services")
                for svc_arn in svc_page.get("serviceArns", []):
                    self._ensure_not_cancelled()
                    svc_name = svc_arn.split("/")[-1]
                    svc_node = self._add_arn_node(svc_arn, label=svc_name, node_type="service")
                    self._node(svc_node, service="ecs")
                    self.store.add_edge(node_id, svc_node, relationship="hosts")

    # ── S3 ───────────────────────────────────────────────────────────────────

    def _scan_s3(self, session: boto3.session.Session) -> None:
        client = self._client(session, "s3")
        self._increment_api_call("s3", "list_buckets")
        response = client.list_buckets()
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

    # ── RDS ──────────────────────────────────────────────────────────────────

    def _scan_rds(self, session: boto3.session.Session) -> None:
        client = self._client(session, "rds")
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
        # Aurora clusters
        try:
            cluster_paginator = client.get_paginator("describe_db_clusters")
            for page in cluster_paginator.paginate():
                self._ensure_not_cancelled()
                self._increment_api_call("rds", "describe_db_clusters")
                for cluster in page.get("DBClusters", []):
                    self._ensure_not_cancelled()
                    arn = cluster.get("DBClusterArn", "")
                    node_id = self._add_arn_node(arn, label=cluster.get("DBClusterIdentifier"), node_type="cluster")
                    self._node(
                        node_id,
                        service="rds",
                        engine=cluster.get("Engine"),
                        state=cluster.get("Status"),
                    )
        except (ClientError, BotoCoreError) as exc:
            logger.debug("RDS cluster scan skipped: %s", exc)

    # ── Step Functions ───────────────────────────────────────────────────────

    def _scan_stepfunctions(self, session: boto3.session.Session) -> None:
        client = self._client(session, "stepfunctions")
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

    # ── SNS ──────────────────────────────────────────────────────────────────

    def _scan_sns(self, session: boto3.session.Session) -> None:
        client = self._client(session, "sns")
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
            arn = f"arn:aws:kinesis:{self._region}:*:stream/{name}"
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

    def _scan_cognito(self, session: boto3.session.Session) -> None:
        client = self._client(session, "cognito-idp")
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
                arn = f"arn:aws:cognito-idp:{self._region}:*:userpool/{pool_id}"
                node_id = self._make_node_id("cognito", pool_id)
                self._node(node_id, label=pool.get("Name", pool_id), service="cognito", type="user_pool", arn=arn)
            next_token = page.get("NextToken")
            if not next_token:
                break

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
                    arn = f"arn:aws:elasticache:{self._region}:*:cluster/{cluster_id}"
                    self._node(node_id, label=cluster_id, service="elasticache", type="cluster", arn=arn)
                self._node(
                    node_id,
                    service="elasticache",
                    engine=cluster.get("Engine"),
                    engine_version=cluster.get("EngineVersion"),
                    node_type=cluster.get("CacheNodeType"),
                    state=cluster.get("CacheClusterStatus"),
                )

    # ── Glue ─────────────────────────────────────────────────────────────────

    def _scan_glue(self, session: boto3.session.Session) -> None:
        client = self._client(session, "glue")
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
                arn = f"arn:aws:glue:{self._region}:*:job/{job_name}"
                node_id = self._make_node_id("glue", job_name)
                self._node(node_id, label=job_name, service="glue", type="job", arn=arn)
            next_token = page.get("NextToken")
            if not next_token:
                break

    # ── AppSync ──────────────────────────────────────────────────────────────

    def _scan_appsync(self, session: boto3.session.Session) -> None:
        client = self._client(session, "appsync")
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
                node_id = self._add_arn_node(arn, label=api.get("name"), node_type="api")
                self._node(node_id, service="appsync", auth_type=api.get("authenticationType"))
            next_token = page.get("nextToken")
            if not next_token:
                break

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
