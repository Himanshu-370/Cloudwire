from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from ._utils import logger


class SqsScannerMixin:
    def _scan_sqs(self, session: "boto3.session.Session") -> None:
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
                queue_arn = f"arn:aws:sqs:{self._region}:{self._account_id}:{queue_name}"
                node_id = self._make_node_id("sqs", queue_url)
                self._node(
                    node_id,
                    label=queue_name,
                    service="sqs",
                    type="queue",
                    queue_url=queue_url,
                    arn=queue_arn,
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
