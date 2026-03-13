from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Set, Tuple

import networkx as nx

logger = logging.getLogger(__name__)


class GraphStore:
    def __init__(self) -> None:
        self.graph = nx.DiGraph()
        self.metadata: Dict[str, Any] = {
            "last_scan_at": None,
            "region": None,
            "scanned_services": [],
            "warnings": [],
        }
        self._lock = Lock()

    def reset(self, *, region: str, services: List[str]) -> None:
        with self._lock:
            self.graph = nx.DiGraph()
            self.metadata = {
                "last_scan_at": datetime.now(timezone.utc).isoformat(),
                "region": region,
                "scanned_services": services,
                "warnings": [],
            }

    def add_warning(self, warning: str) -> None:
        with self._lock:
            self.metadata.setdefault("warnings", []).append(warning)

    def update_metadata(self, **kwargs: Any) -> None:
        with self._lock:
            self.metadata.update(kwargs)

    def add_node(self, node_id: str, **attrs: Any) -> None:
        with self._lock:
            current = self.graph.nodes[node_id] if self.graph.has_node(node_id) else {}
            merged = {**current, **attrs}
            merged["id"] = node_id
            self.graph.add_node(node_id, **merged)

    def add_edge(self, source: str, target: str, **attrs: Any) -> None:
        with self._lock:
            current = self.graph.get_edge_data(source, target, default={})
            merged = {**current, **attrs}
            self.graph.add_edge(source, target, **merged)

    def _serialize_node(self, node_id: str, attrs: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"id": node_id}
        payload.update(attrs)
        return payload

    def _serialize_edge(self, source: str, target: str, attrs: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"id": f"{source}\u2192{target}", "source": source, "target": target}
        payload.update(attrs)
        return payload

    def get_graph_payload(self) -> Dict[str, Any]:
        with self._lock:
            nodes = [self._serialize_node(node_id, attrs) for node_id, attrs in self.graph.nodes(data=True)]
            edges = [
                self._serialize_edge(source, target, attrs)
                for source, target, attrs in self.graph.edges(data=True)
            ]
            metadata = dict(self.metadata)
            metadata["node_count"] = len(nodes)
            metadata["edge_count"] = len(edges)
            return {"nodes": nodes, "edges": edges, "metadata": metadata}

    def iter_nodes_by_service(self, service: str) -> List[Tuple[str, Dict[str, Any]]]:
        """Return a snapshot of (node_id, attrs_copy) pairs for a given service."""
        with self._lock:
            return [
                (nid, dict(attrs))
                for nid, attrs in self.graph.nodes(data=True)
                if attrs.get("service") == service
            ]

    def snapshot_graph(self) -> "nx.DiGraph":
        """Return a shallow copy of the graph for read-only traversal."""
        with self._lock:
            return self.graph.copy()

    def batch_update_nodes(self, updates: List[Tuple[str, Dict[str, Any]]]) -> None:
        """Apply attribute updates to multiple nodes atomically."""
        with self._lock:
            for node_id, attrs in updates:
                if self.graph.has_node(node_id):
                    self.graph.nodes[node_id].update(attrs)

    def _node_matches_arns(self, node_id: str, attrs: Dict[str, Any], allowed_arns: Set[str]) -> bool:
        """Check if a node matches any of the allowed ARNs.

        Tries multiple fields since scanners are inconsistent about ARN storage:
          1. 'real_arn' attribute (set by _fetch_and_apply_tags — always a proper ARN)
          2. 'arn' attribute directly
          3. The embedded ARN in node_id (format 'service:arn')
        Returns False for nodes without any ARN-like attribute (synthetic/connector nodes).
        """
        real_arn = attrs.get("real_arn")
        if real_arn and real_arn in allowed_arns:
            return True
        node_arn = attrs.get("arn")
        if node_arn and node_arn in allowed_arns:
            return True
        arn_in_id = node_id.split(":", 1)[1] if ":" in node_id else ""
        if arn_in_id in allowed_arns:
            return True
        return False

    def filter_by_arns(self, allowed_arns: Set[str]) -> Dict[str, int]:
        """Remove nodes that don't match the allowed ARNs, preserving neighbors.

        Keeps:
          - Nodes whose ARN matches the allowed set (the "seed" nodes)
          - Direct neighbors of seed nodes (1-hop), marked ``kept_by_proximity=True``
          - VPC infrastructure ancestors of kept nodes
        Returns dict with ``seeds``, ``neighbors``, ``removed``, ``total`` counts.
        """
        with self._lock:
            total = self.graph.number_of_nodes()
            logger.debug(
                "filter_by_arns: %d graph nodes, %d allowed ARNs",
                total, len(allowed_arns),
            )
            # Phase 1: identify seed nodes (directly matched by ARN)
            seed_ids: Set[str] = set()
            for node_id, attrs in self.graph.nodes(data=True):
                if self._node_matches_arns(node_id, attrs, allowed_arns):
                    seed_ids.add(node_id)
                # Note: nodes without arn/real_arn that have NO edges are
                # phantom inferred nodes — they will be removed unless they
                # are neighbors of seeds. This avoids polluting the tag graph
                # with Lambda env-var inferred phantom nodes.

            # Phase 2: expand to direct neighbors of seeds (1-hop)
            # Mark them so the frontend can visually distinguish proximity nodes
            neighbor_ids: Set[str] = set()
            for seed_id in seed_ids:
                for neighbor in self.graph.predecessors(seed_id):
                    if neighbor not in seed_ids:
                        neighbor_ids.add(neighbor)
                for neighbor in self.graph.successors(seed_id):
                    if neighbor not in seed_ids:
                        neighbor_ids.add(neighbor)

            keep_ids = seed_ids | neighbor_ids

            # Phase 3: walk VPC infrastructure ancestors so topology context
            # (VPC → subnet → resource, IGW → VPC, RTB → subnet) stays intact.
            vpc_frontier = [
                nid for nid in keep_ids
                if self.graph.nodes[nid].get("service") == "vpc"
            ]
            visited = set(vpc_frontier)
            while vpc_frontier:
                nid = vpc_frontier.pop()
                for neighbor in self.graph.predecessors(nid):
                    if neighbor not in keep_ids and neighbor not in visited:
                        attrs = self.graph.nodes[neighbor]
                        if attrs.get("service") == "vpc":
                            keep_ids.add(neighbor)
                            neighbor_ids.add(neighbor)
                            visited.add(neighbor)
                            vpc_frontier.append(neighbor)
                for neighbor in self.graph.successors(nid):
                    if neighbor not in keep_ids and neighbor not in visited:
                        attrs = self.graph.nodes[neighbor]
                        if attrs.get("service") == "vpc":
                            keep_ids.add(neighbor)
                            neighbor_ids.add(neighbor)
                            visited.add(neighbor)
                            vpc_frontier.append(neighbor)

            # Phase 4: mark proximity nodes and remove everything else
            for nid in neighbor_ids:
                if self.graph.has_node(nid):
                    self.graph.nodes[nid]["kept_by_proximity"] = True

            nodes_to_remove = [
                node_id for node_id in self.graph.nodes()
                if node_id not in keep_ids
            ]
            for node_id in nodes_to_remove:
                self.graph.remove_node(node_id)
            logger.debug(
                "filter_by_arns: seeds=%d, neighbors=%d, kept=%d, removed=%d",
                len(seed_ids), len(neighbor_ids), len(keep_ids), len(nodes_to_remove),
            )
            return {
                "seeds": len(seed_ids),
                "neighbors": len(neighbor_ids),
                "removed": len(nodes_to_remove),
                "total": total,
            }

    def get_resource_payload(self, resource_id: str) -> Dict[str, Any]:
        with self._lock:
            if not self.graph.has_node(resource_id):
                raise KeyError(resource_id)

            node = self._serialize_node(resource_id, dict(self.graph.nodes[resource_id]))
            incoming = [
                self._serialize_edge(source, resource_id, dict(attrs))
                for source, _, attrs in self.graph.in_edges(resource_id, data=True)
            ]
            outgoing = [
                self._serialize_edge(resource_id, target, dict(attrs))
                for _, target, attrs in self.graph.out_edges(resource_id, data=True)
            ]
            return {"node": node, "incoming": incoming, "outgoing": outgoing}
