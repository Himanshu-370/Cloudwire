from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from ._utils import _ARN_PATTERN, logger


class DynamoDBScannerMixin:
    def _scan_dynamodb(self, session: "boto3.session.Session") -> None:
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
                # Construct real ARN for tag filter matching
                table_arn = f"arn:aws:dynamodb:{self._region}:{self._account_id}:table/{name}"
                node_id = self._make_node_id("dynamodb", name)
                self._node(node_id, label=name, service="dynamodb", type="table", arn=table_arn)
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
