export type AskAgenticNodeStatus = "queued" | "active" | "done" | "failed" | "skipped";

export type AskAgenticNodeGroup = "shared" | "conversation" | "standard" | "deep";

export type AskAgenticNodeResources = {
  agents: readonly string[];
  skills: readonly string[];
  tools: readonly string[];
};

export type AskAgenticNode = {
  id: string;
  label: string;
  description: string;
  group: AskAgenticNodeGroup;
  status: AskAgenticNodeStatus;
  resources: AskAgenticNodeResources;
  activity?: string;
};
