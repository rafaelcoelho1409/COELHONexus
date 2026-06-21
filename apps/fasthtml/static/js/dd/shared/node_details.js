// DD Planner/Synth node detail registry.
//
// This feeds the shared NodeDrawer with stable explanations for what each
// graph node actually executes. Runtime output still comes from the existing
// per-node renderers; this file only owns the human-readable action model and
// lightweight LLM activity rollups already present in LangGraph state.

function _num(v) {
  return Number.isFinite(Number(v)) ? Number(v) : 0;
}

function _deploymentCount(list) {
  return Array.isArray(list) ? list.length : 0;
}

function _callsLabel(n, fallback) {
  if (n === undefined || n === null) return fallback || 'not reported';
  const v = _num(n);
  return v === 1 ? '1 call' : v + ' calls';
}

function _metric(label, value, note) {
  return { label, value: String(value), note: note || '' };
}

function _fmtInt(v) {
  const n = Number(v || 0);
  return Number.isFinite(n) ? n.toLocaleString() : '0';
}

function _topModel(byModel) {
  const entries = Object.entries(byModel || {});
  if (!entries.length) return null;
  entries.sort((a, b) => Number((b[1] || {}).calls || 0) - Number((a[1] || {}).calls || 0));
  return entries[0];
}

function _splitProviderModel(model) {
  const raw = String(model || '');
  if (!raw) return { provider: 'unknown', name: 'unknown', raw };
  const lower = raw.toLowerCase();
  if (lower.startsWith('meta-llama/')) {
    return { provider: 'groq', name: raw, raw };
  }
  const idx = raw.indexOf('/');
  if (idx > 0) return { provider: raw.slice(0, idx), name: raw.slice(idx + 1), raw };
  if (lower.startsWith('mistral')) return { provider: 'mistral', name: raw, raw };
  if (lower.startsWith('llama-')) {
    return { provider: 'groq', name: raw, raw };
  }
  if (lower.startsWith('openai/')) {
    return { provider: 'openai', name: raw.slice(7), raw };
  }
  return { provider: 'implicit', name: raw, raw };
}

const PLANNER_DETAILS = {
  corpus_load: {
    title: 'Corpus Load',
    subtitle: 'Reads the selected documentation corpus from storage.',
    kind: 'deterministic I/O',
    actions: [
      'Loads the raw files for the active framework slug.',
      'Computes corpus size, file count, byte distribution, and ingestion metadata.',
      'Publishes raw_files and corpus_stats for downstream nodes.',
    ],
    inputs: ['Framework slug', 'Ingestion storage objects'],
    outputs: ['raw_files', 'corpus_stats'],
    llm: 'No LLM call. This node is storage and metadata work only.',
    metrics(values) {
      const s = values.corpus_stats || {};
      return [
        _metric('files', s.total_files || s.files || 0),
        _metric('bytes', s.total_bytes || 0),
        _metric('token source', 'none', 'no chat or embedding model'),
      ];
    },
  },
  embed_corpus: {
    title: 'Embed Corpus',
    subtitle: 'Creates reusable vector embeddings for every loaded document.',
    kind: 'embedding',
    actions: [
      'Batches document text into the configured NIM embedding model.',
      'Stores the embedding manifest and vector blob for later semantic filtering.',
      'Reuses cached vectors when the corpus manifest hash has already been embedded.',
    ],
    inputs: ['raw_files'],
    outputs: ['embeddings_ref', 'embed_stats'],
    llm: 'Embedding model call, not chat-completion reasoning.',
    metrics(values) {
      const s = values.embed_stats || {};
      return [
        _metric('embedded files', s.files || 0),
        _metric('dimensions', s.dim || 0),
        _metric('cache', s.cache_hit ? 'hit' : 'cold'),
      ];
    },
  },
  off_topic: {
    title: 'Off-Topic Filter',
    subtitle: 'Uses LLM-as-judge routing to keep only relevant corpus pages.',
    kind: 'LLM judge',
    actions: [
      'Judges each document against the framework/domain boundary.',
      'Records KEEP/DROP decisions with deployment, latency, and error telemetry.',
      'Writes the filtered relevant_files set consumed by planning.',
    ],
    inputs: ['raw_files', 'embeddings_ref'],
    outputs: ['relevant_files', 'off_topic_stats'],
    llm: 'Per-document LLM judge calls through the DD grader rotator.',
    metrics(values) {
      const s = values.off_topic_stats || {};
      return [
        _metric('LLM calls', _callsLabel(s.llm_judged, 'pending')),
        _metric('kept/dropped', (s.llm_kept || s.kept || 0) + '/' + (s.llm_dropped || s.dropped || 0)),
        _metric('deployments', _deploymentCount(s.deployment_usage)),
      ];
    },
  },
  doc_distill: {
    title: 'Document Distill',
    subtitle: 'Compresses large corpora into per-document summaries and key terms.',
    kind: 'LLM map',
    actions: [
      'For large corpora, summarizes each relevant document and extracts key terms.',
      'For small corpora, skips distillation so the proposer can read raw bodies directly.',
      'Persists doc_distill_ref plus stats about success, failures, cache, and wall time.',
    ],
    inputs: ['relevant_files'],
    outputs: ['doc_distill_ref', 'doc_distill_stats'],
    llm: 'Parallel per-document LLM calls when the small-corpus bypass is not active.',
    metrics(values) {
      const s = values.doc_distill_stats || {};
      if (s.skipped) {
        return [
          _metric('LLM calls', '0', 'small-corpus bypass'),
          _metric('files', s.n_files || 0),
          _metric('cache', s.cache_hit ? 'hit' : 'not used'),
        ];
      }
      return [
        _metric('distilled', s.n_distilled || 0),
        _metric('failed', s.n_failed || 0),
        _metric('cache', s.cache_hit ? 'hit' : 'cold'),
      ];
    },
  },
  chapter_propose: {
    title: 'Chapter Propose',
    subtitle: 'Asks the rotator to propose candidate study chapters.',
    kind: 'LLM synthesis',
    actions: [
      'Builds a long-context planning prompt from distillates, headings, and file-tree structure.',
      'Samples candidate chapter sets and selects the best one with a consistency vote.',
      'Writes chapter_proposals_ref and proposal-level provenance.',
    ],
    inputs: ['doc_distill_ref or relevant_files'],
    outputs: ['chapter_proposals_ref', 'propose_stats'],
    llm: 'Long-context LLM sampling plus vote/repair calls when needed.',
    metrics(values) {
      const s = values.propose_stats || {};
      return [
        _metric('proposals', s.n_proposals || 0),
        _metric('valid samples', s.n_samples_valid !== undefined ? s.n_samples_valid + '/3' : 'pending'),
        _metric('chosen sample', s.chosen_idx ?? 'pending'),
      ];
    },
  },
  chapter_assign: {
    title: 'Chapter Assign',
    subtitle: 'Scores every document against every proposed chapter.',
    kind: 'LLM classifier',
    actions: [
      'Runs a per-document structured LLM call that returns chapter confidence scores.',
      'Allows multi-assignment when a page belongs to more than one chapter.',
      'Falls back to lexical rescue only when a document has content but the assign call fails.',
    ],
    inputs: ['chapter_proposals_ref', 'relevant_files or doc_distill_ref'],
    outputs: ['chapter_doc_assignments_ref', 'assign_stats'],
    llm: 'One short structured LLM call per document, executed with bounded concurrency.',
    metrics(values) {
      const s = values.assign_stats || {};
      return [
        _metric('docs scored', s.n_docs || 0),
        _metric('assigned', s.n_assigned || 0),
        _metric('failed', s.n_failed || 0),
      ];
    },
  },
  chapter_select: {
    title: 'Chapter Select',
    subtitle: 'Selects the final chapter set with deterministic coverage logic.',
    kind: 'algorithm',
    actions: [
      'Chooses a minimum useful chapter set from the proposal/assignment matrix.',
      'Targets broad document coverage while pruning tiny unpinned chapters.',
      'Produces the chapter_plan_ref consumed by ordering and final plan writing.',
    ],
    inputs: ['chapter_doc_assignments_ref'],
    outputs: ['chapter_plan_ref', 'select_stats'],
    llm: 'No LLM call. Selection is greedy coverage and pruning logic.',
    metrics(values) {
      const s = values.select_stats || {};
      const pct = s.coverage_fraction !== undefined
        ? Math.round(s.coverage_fraction * 100) + '%'
        : 'pending';
      return [
        _metric('chapters out', s.n_chapters_out || 0),
        _metric('coverage', pct),
        _metric('pruned', s.n_pruned || 0),
      ];
    },
  },
  order_chapters: {
    title: 'Order Chapters',
    subtitle: 'Ranks the selected chapters into a pedagogical sequence.',
    kind: 'LLM ranking',
    actions: [
      'Samples independent chapter orderings from the LLM rotator.',
      'Pins foundational chapters first when the ranking panel identifies prerequisites.',
      'Aggregates samples with Borda scoring into chapter_order_ref.',
    ],
    inputs: ['chapter_plan_ref'],
    outputs: ['chapter_order_ref', 'order_chapters_stats'],
    llm: 'Small panel of LLM ranking calls plus deterministic Borda aggregation.',
    metrics(values) {
      const s = values.order_chapters_stats || {};
      return [
        _metric('ranking calls', _callsLabel(s.n_samples, 'pending')),
        _metric('chapters', s.n_chapters || 0),
        _metric('deployments', _deploymentCount(s.deployment_usage)),
      ];
    },
  },
  plan_write: {
    title: 'Plan Write',
    subtitle: 'Materializes the final study plan for Synth and the reader UI.',
    kind: 'deterministic writer',
    actions: [
      'Combines selected, ordered chapters with hydrated source references.',
      'Drops empty chapters and records unassigned/dropped source counts.',
      'Writes plan_path and the latest plan pointer used by DD Study/Synth.',
    ],
    inputs: ['chapter_order_ref or chapter_plan_ref'],
    outputs: ['plan_path', 'plan_write_stats'],
    llm: 'No LLM call. This node formats and persists the final plan.',
    metrics(values) {
      const s = values.plan_write_stats || {};
      return [
        _metric('chapters', s.n_chapters || ((s.plan || {}).chapters || []).length || 0),
        _metric('sources', s.n_sources || 0),
        _metric('unassigned', s.n_unassigned || 0),
      ];
    },
  },
};

const SYNTH_DETAILS = {
  outline_sdp: {
    title: 'Outline (Structure-Driven Planner)',
    subtitle: 'Builds a typed section graph for one chapter.',
    kind: 'LLM planner',
    actions: [
      'Calls the outline planner to propose sections and prerequisites for the chapter.',
      'Derives a DAG, stage indices, cycle repairs, and structural validation results.',
      'Feeds same-stage parallelism to the writer node.',
    ],
    inputs: ['planner chapter', 'vault/code context'],
    outputs: ['outline_path', 'outline_stats'],
    llm: 'SDP means Structure-Driven Planner in the backend node. It is an LLM outline call plus deterministic graph derivation.',
    metrics(values) {
      const s = values.outline_stats || {};
      return [
        _metric('samples', s.n_samples || 1),
        _metric('sections', s.n_sections || 0),
        _metric('violations', s.n_violations || 0),
      ];
    },
  },
  digest_construct: {
    title: 'Digest Construct',
    subtitle: 'Routes evidence, source summaries, and code references into outline sections.',
    kind: 'evidence builder',
    actions: [
      'Builds the per-section evidence digest consumed by the writer.',
      'Routes source documents and vault code references to the sections that need them.',
      'Flags orphan refs, empty sections, and over-spread sources for operator review.',
    ],
    inputs: ['outline_path', 'planner sources', 'vault entries'],
    outputs: ['digest_path', 'digest_stats'],
    llm: 'Uses LLM-assisted routing/digest work where configured; deterministic aggregation handles coverage stats.',
    metrics(values) {
      const s = values.digest_stats || {};
      return [
        _metric('sources', s.n_sources || 0),
        _metric('coverage', (s.n_sections_covered || 0) + '/' + (s.n_sections || 0)),
        _metric('orphan refs', s.n_orphan_code_refs || 0),
      ];
    },
  },
  sawc_write: {
    title: 'SAWC Write — Section-Aware Writer-Critic',
    subtitle: 'Writes chapter sections with staged drafts, critic selection, and repairs.',
    kind: 'LLM writer/critic',
    actions: [
      'Runs stage-parallel best-of-N section drafts against the outline and digest.',
      'Uses a critic picker to choose the best draft and records fallback decisions.',
      'Extracts memory for later sections and re-enters when MGSR requests refinement.',
    ],
    inputs: ['outline_path', 'digest_path', 'refine actions'],
    outputs: ['sawc_path', 'sawc_stats'],
    llm: 'SAWC is shown here as Section-Aware Writer-Critic: a section-level writer plus critic-picker for material synthesis.',
    metrics(values) {
      const s = values.sawc_stats || {};
      return [
        _metric('draft calls', s.n_total_drafts_fired || 0),
        _metric('critic picks', s.n_critic_picks || 0),
        _metric('repairs', s.n_repairs || 0),
      ];
    },
  },
  sawc_derive: {
    title: 'SAWC Derive',
    subtitle: 'Optionally derives extra analogical code examples for subtopics.',
    kind: 'optional LLM expansion',
    actions: [
      'Attempts analogical code generation for eligible SAWC subtopics.',
      'Rejects candidates that fail AST, length, or quality gates.',
      'Promotes accepted examples into the chapter materialization path.',
    ],
    inputs: ['sawc_path', 'vault entries'],
    outputs: ['derive_stats'],
    llm: 'Optional rotator calls, controlled by KD_SAWC_DERIVE.',
    metrics(values) {
      const s = values.derive_stats || {};
      if (s.enabled === false) {
        return [
          _metric('LLM calls', '0', 'disabled'),
          _metric('subtopics', s.n_subtopics_total || 0),
          _metric('promoted', 0),
        ];
      }
      return [
        _metric('promoted', s.n_promoted || 0),
        _metric('rotator fail', s.n_rotator_fail || 0),
        _metric('attempts', Array.isArray(s.attempts) ? s.attempts.length : 'not reported'),
      ];
    },
  },
  checklist_eval: {
    title: 'Checklist Eval',
    subtitle: 'Evaluates the drafted chapter against deterministic and LLM criteria.',
    kind: 'hybrid evaluator',
    actions: [
      'Runs pre-gate deterministic checks before spending judge calls.',
      'Uses an LLM judge for criteria that require semantic reading.',
      'Produces pass/fail feedback used by MGSR.',
    ],
    inputs: ['sawc_path'],
    outputs: ['checklist_path', 'checklist_stats'],
    llm: 'LLM judge calls only for criteria that survive the pre-gate.',
    metrics(values) {
      const s = values.checklist_stats || {};
      return [
        _metric('LLM judged', (s.n_llm_passed || 0) + '/' + (s.n_llm_total || 0)),
        _metric('pre-gate', (s.n_pregate_passed || 0) + '/' + (s.n_pregate_total || 0)),
        _metric('pass rate', s.pass_rate !== undefined ? Math.round(s.pass_rate * 100) + '%' : 'pending'),
      ];
    },
  },
  mgsr_replan: {
    title: 'Memory-Guided Structure Replanner',
    subtitle: 'Decides whether the chapter should halt or loop back for refinement.',
    kind: 'LLM replan',
    actions: [
      'Short-circuits without an LLM call when the checklist already passes.',
      'Otherwise asks for typed refinement actions targeted at failed criteria.',
      'Routes the graph either to render_audit_write or back to sawc_write.',
    ],
    inputs: ['checklist_path', 'sawc_path'],
    outputs: ['mgsr_path', 'mgsr_stats'],
    llm: 'Fast path has no LLM call; replan path uses one structured LLM call.',
    metrics(values) {
      const s = values.mgsr_stats || {};
      return [
        _metric('decision', s.halt === undefined ? 'pending' : s.halt ? 'halt' : 'loop'),
        _metric('LLM path', s.trivial_pass ? '0 calls' : s.halt === undefined ? 'pending' : '1 call'),
        _metric('actions', s.n_actions || 0),
      ];
    },
  },
  render_audit_write: {
    title: 'Render and Audit Write',
    subtitle: 'Materializes the final chapter and verifies vault/code-reference integrity.',
    kind: 'deterministic render',
    actions: [
      'Renders the final markdown artifacts from SAWC output and resolved vault refs.',
      'Audits code-ref resolution, sentinel replacement, byte drift, and missing refs.',
      'Writes the chapter_path consumed by DD Study.',
    ],
    inputs: ['sawc_path', 'derive_stats', 'mgsr_path'],
    outputs: ['chapter_path', 'chapter_stats'],
    llm: 'No LLM call. This node renders, audits, and persists artifacts.',
    metrics(values) {
      const s = values.chapter_stats || {};
      return [
        _metric('audit', s.audit_passed === undefined ? 'pending' : s.audit_passed ? 'pass' : 'fail'),
        _metric('refs', (s.n_resolved || 0) + '/' + (s.n_code_refs || 0)),
        _metric('artifacts', s.n_artifacts || 0),
      ];
    },
  },
};

export function getDdNodeDetails(stage, nodeId) {
  const table = stage === 'synth' ? SYNTH_DETAILS : PLANNER_DETAILS;
  return table[nodeId] || null;
}

export function buildDdNodeMetrics(stage, nodeId, values) {
  const details = getDdNodeDetails(stage, nodeId);
  if (!details || typeof details.metrics !== 'function') return [];
  try {
    return details.metrics(values || {}).filter(Boolean);
  } catch (err) {
    console.warn('[dd-node-details] failed to build metrics', stage, nodeId, err);
    return [];
  }
}

export function buildDdTokenMetrics(stage, nodeId, counters) {
  const byNode = (counters && counters.by_node) || {};
  const node = byNode[nodeId] || null;
  if (!node || !Number(node.calls || 0)) return [];
  const out = [
    _metric('LLM calls', _fmtInt(node.calls), 'exact rotator usage'),
    _metric('input tokens', _fmtInt(node.tokens_in)),
    _metric('output tokens', _fmtInt(node.tokens_out)),
  ];
  if (Number(node.reasoning_tokens || 0) > 0) {
    out.push(_metric('reasoning tokens', _fmtInt(node.reasoning_tokens)));
  }
  const top = _topModel(node.by_model);
  if (top) {
    out.push(_metric(
      'top model',
      String(top[0]).split('/').slice(-1)[0],
      _fmtInt((top[1] || {}).calls) + ' calls',
    ));
  }
  return out;
}

export function buildDdModelRows(stage, nodeId, counters) {
  const byNode = (counters && counters.by_node) || {};
  const node = byNode[nodeId] || null;
  if (!node || !node.by_model) return [];
  return Object.entries(node.by_model)
    .map(([model, stats]) => {
      const split = _splitProviderModel(model);
      return {
        raw: split.raw,
        provider: split.provider,
        model: split.name,
        calls: _num((stats || {}).calls),
        tokens_in: _num((stats || {}).tokens_in),
        tokens_out: _num((stats || {}).tokens_out),
        reasoning_tokens: _num((stats || {}).reasoning_tokens),
      };
    })
    .filter(row => row.calls > 0)
    .sort((a, b) => b.calls - a.calls);
}
