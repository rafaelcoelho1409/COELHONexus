/** Shared shape for the per-page Agentic AI quality summary shown in readers. */
export type PageQualityIssue = {
  issue_id: string;
  code: string;
  severity: string;
  description: string;
  evidence: string[];
  resolution: "auto_applied" | "review" | "none";
  proposal: {
    target_text: string;
    replacement: string;
    rationale: string;
    confidence: number;
    risk: string;
  } | null;
};

export type PageQualityReport = {
  status: "done" | "review_required";
  delivery_disposition: "include" | "discarded_blank" | "image_only";
  issues: PageQualityIssue[];
  mechanical_fixes: Readonly<Record<string, number>>;
  audit_warnings: string[];
  errors: string[];
};
