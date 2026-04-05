"""Map Cost Explorer resource IDs to CloudWire graph node IDs."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CostMapping:
    node_costs: Dict[str, float] = field(default_factory=dict)
    service_totals: Dict[str, float] = field(default_factory=dict)
    unmatched_resource_ids: List[str] = field(default_factory=list)
    period: str = ""


def _extract_from_arn(arn: str, separator: str = "/") -> str:
    """Extract the resource identifier from an ARN's last segment."""
    if separator in arn:
        return arn.rsplit(separator, 1)[-1]
    return arn.rsplit(":", 1)[-1]


class CostMapper:
    """Maps CE resource IDs to graph node IDs using service-specific strategies."""

    def __init__(
        self,
        nodes_by_service: Dict[str, List[Tuple[str, Dict[str, Any]]]],
    ) -> None:
        self._nodes_by_service = nodes_by_service
        self._build_indices()

    def _build_indices(self) -> None:
        """Build lookup indices for each supported service."""
        # EC2: node_id = "ec2:{instance_id}"
        self._ec2_by_id: Dict[str, str] = {}
        for node_id, attrs in self._nodes_by_service.get("ec2", []):
            # Extract instance ID from node_id like "ec2:i-0abc123"
            suffix = node_id.split(":", 1)[1] if ":" in node_id else ""
            if suffix.startswith("i-"):
                self._ec2_by_id[suffix] = node_id

        # RDS: node_id = "rds:arn:aws:rds:..." or "rds:{identifier}"
        self._rds_by_arn: Dict[str, str] = {}
        self._rds_by_id: Dict[str, str] = {}
        for node_id, attrs in self._nodes_by_service.get("rds", []):
            arn = attrs.get("arn") or attrs.get("real_arn", "")
            if arn:
                self._rds_by_arn[arn] = node_id
            label = attrs.get("label", "")
            if label:
                self._rds_by_id[label] = node_id

        # S3: node_id = "s3:{bucket_name}"
        self._s3_by_name: Dict[str, str] = {}
        for node_id, attrs in self._nodes_by_service.get("s3", []):
            suffix = node_id.split(":", 1)[1] if ":" in node_id else ""
            if suffix:
                self._s3_by_name[suffix] = node_id

        # DynamoDB: node_id = "dynamodb:{table_name}" (quick) or "dynamodb:arn:..." (deep)
        self._dynamo_by_arn: Dict[str, str] = {}
        self._dynamo_by_name: Dict[str, str] = {}
        for node_id, attrs in self._nodes_by_service.get("dynamodb", []):
            arn = attrs.get("arn") or attrs.get("real_arn", "")
            if arn:
                self._dynamo_by_arn[arn] = node_id
            label = attrs.get("label", "")
            if label:
                self._dynamo_by_name[label] = node_id
            # Also try the node_id suffix
            suffix = node_id.split(":", 1)[1] if ":" in node_id else ""
            if suffix and not suffix.startswith("arn:"):
                self._dynamo_by_name[suffix] = node_id

        # ElastiCache: node_id = "elasticache:arn:..." or "elasticache:{cluster_id}"
        self._ec_by_arn: Dict[str, str] = {}
        self._ec_by_id: Dict[str, str] = {}
        for node_id, attrs in self._nodes_by_service.get("elasticache", []):
            arn = attrs.get("arn") or attrs.get("real_arn", "")
            if arn:
                self._ec_by_arn[arn] = node_id
            label = attrs.get("label", "")
            if label:
                self._ec_by_id[label] = node_id

        # Redshift: node_id = "redshift:{cluster_id}"
        self._redshift_by_id: Dict[str, str] = {}
        for node_id, attrs in self._nodes_by_service.get("redshift", []):
            suffix = node_id.split(":", 1)[1] if ":" in node_id else ""
            if suffix:
                self._redshift_by_id[suffix] = node_id
            label = attrs.get("label", "")
            if label:
                self._redshift_by_id[label] = node_id

    def match_ec2(self, ce_resource_id: str) -> Optional[str]:
        """CE returns instance ID like 'i-0abc123def'."""
        return self._ec2_by_id.get(ce_resource_id)

    def match_rds(self, ce_resource_id: str) -> Optional[str]:
        """CE returns full ARN for RDS."""
        node = self._rds_by_arn.get(ce_resource_id)
        if node:
            return node
        # Try matching by DB identifier extracted from the ARN
        db_id = _extract_from_arn(ce_resource_id, "/")
        if ":" in db_id:
            db_id = db_id.rsplit(":", 1)[-1]
        return self._rds_by_id.get(db_id)

    def match_s3(self, ce_resource_id: str) -> Optional[str]:
        """CE returns bucket name."""
        return self._s3_by_name.get(ce_resource_id)

    def match_dynamodb(self, ce_resource_id: str) -> Optional[str]:
        """CE returns table ARN like 'arn:aws:dynamodb:...:table/TableName'."""
        # Try full ARN match first
        node = self._dynamo_by_arn.get(ce_resource_id)
        if node:
            return node
        # Extract table name from ARN
        table_name = _extract_from_arn(ce_resource_id, "/")
        return self._dynamo_by_name.get(table_name)

    def match_elasticache(self, ce_resource_id: str) -> Optional[str]:
        """CE returns cluster ARN."""
        node = self._ec_by_arn.get(ce_resource_id)
        if node:
            return node
        # Extract cluster ID from ARN
        cluster_id = _extract_from_arn(ce_resource_id, ":")
        return self._ec_by_id.get(cluster_id)

    def match_redshift(self, ce_resource_id: str) -> Optional[str]:
        """CE returns cluster ARN."""
        # Extract cluster ID from ARN like arn:aws:redshift:...:cluster:mycluster
        cluster_id = _extract_from_arn(ce_resource_id, ":")
        node = self._redshift_by_id.get(cluster_id)
        if node:
            return node
        # Also try last / segment
        cluster_id = _extract_from_arn(ce_resource_id, "/")
        return self._redshift_by_id.get(cluster_id)

    def match_resource(self, ce_resource_id: str) -> Optional[str]:
        """Try all service matchers against a CE resource ID."""
        # EC2 instance IDs are easy to identify
        if ce_resource_id.startswith("i-"):
            return self.match_ec2(ce_resource_id)

        # S3 bucket names don't contain colons or slashes
        if not ce_resource_id.startswith("arn:") and "/" not in ce_resource_id:
            result = self.match_s3(ce_resource_id)
            if result:
                return result

        # ARN-based matching
        if "arn:" in ce_resource_id:
            if ":rds:" in ce_resource_id:
                return self.match_rds(ce_resource_id)
            if ":dynamodb:" in ce_resource_id:
                return self.match_dynamodb(ce_resource_id)
            if ":elasticache:" in ce_resource_id:
                return self.match_elasticache(ce_resource_id)
            if ":redshift:" in ce_resource_id:
                return self.match_redshift(ce_resource_id)
            # EC2 ARNs (less common in CE, but handle them)
            if ":ec2:" in ce_resource_id and "instance/" in ce_resource_id:
                instance_id = ce_resource_id.rsplit("/", 1)[-1]
                return self.match_ec2(instance_id)

        # EBS volume IDs — no scanner for these yet, skip
        if ce_resource_id.startswith("vol-"):
            return None

        return None

    def map_costs(
        self,
        resource_costs: Dict[str, float],
        service_totals: Dict[str, float],
        period: str,
    ) -> CostMapping:
        """Map CE cost data to graph node IDs."""
        mapping = CostMapping(period=period)
        mapping.service_totals = dict(service_totals)

        for ce_id, amount in resource_costs.items():
            node_id = self.match_resource(ce_id)
            if node_id:
                mapping.node_costs[node_id] = (
                    mapping.node_costs.get(node_id, 0.0) + amount
                )
            else:
                mapping.unmatched_resource_ids.append(ce_id)

        if mapping.unmatched_resource_ids:
            logger.debug(
                "Cost mapper: %d resources unmatched out of %d",
                len(mapping.unmatched_resource_ids),
                len(resource_costs),
            )

        return mapping
