import React, { useCallback } from "react";
import { getServiceVisual } from "../../lib/serviceVisuals.jsx";

const MINI_W = 180;
const MINI_H = 110;

export function Minimap({ nodes, viewport, containerRef, onPan }) {
  const containerWidth = containerRef?.current?.clientWidth || 800;
  const containerHeight = containerRef?.current?.clientHeight || 600;

  if (!nodes.length) return null;

  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const n of nodes) {
    if (n.position.x < minX) minX = n.position.x;
    if (n.position.x > maxX) maxX = n.position.x;
    if (n.position.y < minY) minY = n.position.y;
    if (n.position.y > maxY) maxY = n.position.y;
  }
  minX -= 80; maxX += 80; minY -= 80; maxY += 80;
  const gW = Math.max(1, maxX - minX);
  const gH = Math.max(1, maxY - minY);

  const miniScale = Math.min(MINI_W / gW, MINI_H / gH);

  const toMini = (gx, gy) => ({
    x: (gx - minX) * miniScale,
    y: (gy - minY) * miniScale,
  });

  const vpLeft = -viewport.x / viewport.scale;
  const vpTop = -viewport.y / viewport.scale;
  const vpRight = (containerWidth - viewport.x) / viewport.scale;
  const vpBottom = (containerHeight - viewport.y) / viewport.scale;

  const vpMini = toMini(vpLeft, vpTop);
  const vpW = Math.max(4, (vpRight - vpLeft) * miniScale);
  const vpH = Math.max(4, (vpBottom - vpTop) * miniScale);

  const handleClick = useCallback(
    (e) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      onPan(mx / miniScale + minX, my / miniScale + minY);
    },
    [miniScale, minX, minY, onPan]
  );

  return (
    <div className="minimap-shell">
      <div className="minimap-label">OVERVIEW</div>
      <svg width={MINI_W} height={MINI_H} className="minimap-svg" onClick={handleClick}>
        {nodes.map((node) => {
          const visual = getServiceVisual(node.service);
          const pos = toMini(node.position.x, node.position.y);
          const r = node.type === "cluster" ? 4 : 2;
          return (
            <circle key={node.id} cx={pos.x} cy={pos.y} r={r} fill={visual.color} opacity={0.65} />
          );
        })}
        <rect
          x={Math.max(0, vpMini.x)}
          y={Math.max(0, vpMini.y)}
          width={Math.min(MINI_W - Math.max(0, vpMini.x), vpW)}
          height={Math.min(MINI_H - Math.max(0, vpMini.y), vpH)}
          fill="rgba(255,153,0,0.07)"
          stroke="#ff9900"
          strokeWidth="0.8"
          strokeOpacity="0.6"
          style={{ pointerEvents: "none" }}
        />
      </svg>
    </div>
  );
}
