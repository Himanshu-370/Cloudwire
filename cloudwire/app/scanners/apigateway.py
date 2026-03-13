"""API Gateway (v2 + REST) scanner mixin for AWSGraphScanner."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ._utils import logger, _ARN_PATTERN


class ApiGatewayScannerMixin:
    """Mixin that adds API Gateway scanning capabilities."""

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
                api_arn = f"arn:aws:apigateway:{self._region}::/apis/{api_id}"
                node_id = self._make_node_id("apigateway", api_id)
                self._node(
                    node_id,
                    label=api_name,
                    service="apigateway",
                    type="api",
                    arn=api_arn,
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
                rest_api_arn = f"arn:aws:apigateway:{self._region}::/restapis/{rest_api_id}"
                api_node = self._make_node_id("apigateway", rest_api_id)
                self._node(
                    api_node,
                    label=api.get("name") or rest_api_id,
                    service="apigateway",
                    type="api",
                    arn=rest_api_arn,
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
