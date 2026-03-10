from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List

import networkx as nx


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
        payload = {"id": f"{source}__{target}", "source": source, "target": target}
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
