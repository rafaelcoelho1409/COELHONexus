"use client";

import { useEffect } from "react";

import { useUpdateNodeInternals } from "@xyflow/react";

/**
 * React Flow 12 can lose measured handle bounds when controlled node updates race initial measurement.
 * Re-measure after each committed graph update so connected edges recover from that race.
 */
export function ReactFlowNodeInternals({
  nodeIds,
  revision,
}: {
  nodeIds: readonly string[];
  revision: unknown;
}) {
  const updateNodeInternals = useUpdateNodeInternals();
  const nodeIdsKey = nodeIds.join("\u0000");

  useEffect(() => {
    if (revision === undefined) return;

    const frame = window.requestAnimationFrame(() => {
      updateNodeInternals(nodeIdsKey ? nodeIdsKey.split("\u0000") : []);
    });

    return () => window.cancelAnimationFrame(frame);
  }, [nodeIdsKey, revision, updateNodeInternals]);

  return null;
}
