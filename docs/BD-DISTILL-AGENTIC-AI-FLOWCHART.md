# BD Distill and Ingestion Retrieval flowchart

Solid paths show implemented structure. Dashed paths show later work that is not wired into Distill yet. Distill execution is available by default.

```mermaid
flowchart TB
  subgraph ING["1 · Ingestion: prepare and select the book"]
    UP["User uploads book"]
    EXT["Extract pages and book structure"]
    QA["Ingestion Agentic AI<br/>audit → repair → normalize"]
    CLASS["Classify each page<br/>uncertain or image-only pages stay included"]
    MAN["Publish selection revision<br/>keep every original page"]
    REVIEW["Review included and excluded pages"]
    REV["Save changes as a new revision"]
    READY["Ready for Distill<br/>source pages + QA + chosen selection"]

    UP --> EXT --> QA --> CLASS --> MAN --> REVIEW
    REVIEW -->|"change decisions"| REV --> REVIEW
    REVIEW -->|"accept selection"| READY
  end

  subgraph DIS["2 · Distill: create the source-grounded Learning Guide"]
    PIN["Pin extraction, QA, and selection revisions"]
    PREP["prepare_units<br/>use selected pages; preserve physical page numbers"]
    UNITS["extract_unit<br/>parallel lesson extraction"]
    VERIFY["verify_unit<br/>check source support"]
    CHAPTERS["prepare_chapters<br/>group by book structure"]
    SYNCH["synthesize_chapter<br/>write text-first chapter guides"]
    VISUALS["build_chapter_visuals<br/>build and validate chapter visuals"]
    SYNBOOK["synthesize_book<br/>connect supported ideas across chapters"]
    BRIEF["write_decision_brief<br/>write a separate cited brief"]
    RESEARCH["research_context<br/>optional, separately attributed context"]
    PUBLISH["publish_report<br/>validate evidence, citations, and coverage"]
    GUIDE["Learning Guide<br/>JSON + HTML; PDF on demand"]

    PIN --> PREP --> UNITS --> VERIFY --> CHAPTERS --> SYNCH --> VISUALS --> SYNBOOK --> BRIEF --> RESEARCH --> PUBLISH --> GUIDE
  end

  READY --> PIN

  subgraph RAG["3 · Ingestion retrieval branch: build the index"]
    QUEUE["Queue after Ingestion completes"]
    BUILD["Pin processed pages"]
    ES["Elasticsearch<br/>full-text index"]
    QDRANT["Qdrant<br/>vector index"]
    NEO4J["Neo4j<br/>LLMGraphTransformer graph"]
    PUBLISH_RAG["Publish retrieval index"]
    STATUS["Independent status and controls<br/>under Ingestion"]
    CHAT["BD Ask<br/>current book index"]

    QUEUE --> BUILD --> ES
    ES --> QDRANT
    ES --> NEO4J
    QDRANT --> PUBLISH_RAG
    NEO4J --> PUBLISH_RAG
    PUBLISH_RAG --> STATUS --> CHAT
  end

  READY -->|"enqueue after Ingestion publication"| QUEUE

  subgraph LATER["Optional extensions"]
    SETTINGS["Settings: connect and test<br/>COELHO Search MCP, Tavily, Exa, Jina"]
    EVAL["Evaluate source-only guides<br/>coverage, citations, learning outcomes"]
    RESEARCH["Dated web research<br/>keep external evidence distinct"]
    FUTURE["Future guide with a separate<br/>research section"]
  end

  GUIDE -.-> EVAL
  SETTINGS -.->|"after evaluation; not yet used by Distill"| RESEARCH
  SYNBOOK -.-> RESEARCH
  RESEARCH -.-> FUTURE

  classDef current fill:#e8f3ff,stroke:#2878b8,color:#102a43
  classDef later fill:#f5f5f5,stroke:#8a8a8a,color:#444
  class UP,EXT,QA,CLASS,MAN,REVIEW,REV,READY,PIN,PREP,UNITS,VERIFY,CHAPTERS,SYNCH,VISUALS,SYNBOOK,BRIEF,RESEARCH,PUBLISH,GUIDE,QUEUE,BUILD,ES,QDRANT,NEO4J,PUBLISH_RAG,STATUS,SETTINGS current
  class CHAT,EVAL,RESEARCH,FUTURE later
```

## Key boundary

Distill creates the Learning Guide from the pinned selection. Agentic RAG runs as an independent branch after Ingestion publishes processed pages; it indexes those pages in Elasticsearch, Qdrant, and Neo4j. The retrieval process is displayed under Ingestion and does not delay Distill report publication.

Search connections can be configured and tested in Settings, but Distill does not call them yet. Add web research after evaluating the source-only guide, and keep external findings separate from what the book says.
