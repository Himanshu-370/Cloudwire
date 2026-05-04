"""Orchestrates cost enrichment: fetch from CE, map to nodes, update graph."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import boto3

from ..graph_store import GraphStore
from .cache import cost_cache
from .ce_client import (
    RESOURCE_LEVEL_SERVICES,
    CostResult,
    fetch_resource_costs,
    fetch_service_costs,
)
from .cost_mapper import CostMapper

logger = logging.getLogger(__name__)


@dataclass
class CostEnrichmentResult:
    nodes_enriched: int = 0
    services_with_totals: int = 0
    resource_level_available: bool = False
    unmatched_count: int = 0
    period: str = ""
    warnings: List[str] = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


def enrich_graph_with_costs(
    graph_store: GraphStore,
    session: boto3.session.Session,
    region: str,
    account_id: str,
) -> CostEnrichmentResult:
    """Fetch costs from CE and apply them to graph nodes.

    This is a post-scan enrichment step. It never raises — all errors
    are captured in the result's warnings list.
    """
    result = CostEnrichmentResult()

    # Check cache first
    cached = cost_cache.get(account_id, region)
    if cached:
        service_result, resource_result = cached
        logger.info("Cost data served from cache for %s/%s", account_id, region)
    else:
        # Fetch service-level costs (always works if CE is enabled)
        service_result = fetch_service_costs(session, region)
        if service_result.error:
            result.warnings.append(service_result.error)

        # Try resource-level costs
        resource_result = fetch_resource_costs(session, region)
        if resource_result.error:
            result.warnings.append(resource_result.error)

        # Cache regardless of errors — avoid hammering CE
        cost_cache.put(account_id, region, service_result, resource_result)

    result.resource_level_available = resource_result.resource_level_available
    result.period = f"{service_result.period_start} to {service_result.period_end}"

    # If both failed entirely (e.g., CE not activated), bail
    if not service_result.service_totals and not resource_result.resource_costs:
        if not result.warnings:
            result.warnings.append(
                "No cost data available. Ensure AWS Cost Explorer is activated."
            )
        return result

    # Build mapper from current graph nodes — include all scanned services,
    # not just RESOURCE_LEVEL_SERVICES, so annotation banners can find nodes
    all_services = set(service_result.service_totals.keys()) | set(RESOURCE_LEVEL_SERVICES)
    scanned = graph_store.metadata.get("scanned_services", [])
    if scanned:
        all_services.update(scanned)
    nodes_by_service: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for service in all_services:
        nodes_by_service[service] = graph_store.iter_nodes_by_service(service)

    mapper = CostMapper(nodes_by_service)
    mapping = mapper.map_costs(
        resource_costs=resource_result.resource_costs,
        service_totals=service_result.service_totals,
        period=result.period,
    )

    # Apply per-node costs
    if mapping.node_costs:
        updates = [
            (node_id, {"cost_usd": round(amount, 2), "cost_period": result.period})
            for node_id, amount in mapping.node_costs.items()
        ]
        graph_store.batch_update_nodes(updates)
        result.nodes_enriched = len(updates)

    result.unmatched_count = len(mapping.unmatched_resource_ids)
    result.services_with_totals = len(mapping.service_totals)

    # Store cost metadata on the graph
    graph_store.update_metadata(
        cost_service_totals=mapping.service_totals,
        cost_period=result.period,
        cost_resource_level_available=resource_result.resource_level_available,
        cost_nodes_enriched=result.nodes_enriched,
        cost_unmatched=result.unmatched_count,
    )

    logger.info(
        "Cost enrichment: %d nodes enriched, %d service totals, %d unmatched, period=%s",
        result.nodes_enriched,
        result.services_with_totals,
        result.unmatched_count,
        result.period,
    )

    return result
