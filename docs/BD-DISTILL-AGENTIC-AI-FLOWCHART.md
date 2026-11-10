# BD Distill Agentic AI flowchart

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
    UNITS["extract_unit<br/>parallel learning-unit extraction"]
    CHAPTERS["prepare_chapters<br/>group by book structure"]
    SYNCH["synthesize_chapter<br/>parallel chapter and appendix guides"]
    SYNBOOK["synthesize_book<br/>connect supported ideas across chapters"]
    PUBLISH["publish_report<br/>validate evidence, citations, and coverage"]
    GUIDE["Learning Guide<br/>JSON + HTML; PDF on demand"]

    PIN --> PREP --> UNITS --> CHAPTERS --> SYNCH --> SYNBOOK --> PUBLISH --> GUIDE
  end

  READY --> PIN

  subgraph RAG["3 · After publication: build the retrieval index"]
    QUEUE["Separate indexing job"]
    BUILD["Index normalized pages<br/>including excluded pages"]
    DERIVED["Index learning units and Learning Guide"]
    QDRANT["Hybrid Qdrant index"]
    STATUS["Separate index status and retry"]
    CHAT["Cited book chat<br/>later"]

    QUEUE --> BUILD --> QDRANT
    QUEUE --> DERIVED --> QDRANT
    QDRANT --> STATUS
    QDRANT -.-> CHAT
  end

  GUIDE -->|"enqueue after publication"| QUEUE
  READY --> BUILD
  UNITS --> DERIVED
  GUIDE --> DERIVED

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
  class UP,EXT,QA,CLASS,MAN,REVIEW,REV,READY,PIN,PREP,UNITS,CHAPTERS,SYNCH,SYNBOOK,PUBLISH,GUIDE,QUEUE,BUILD,DERIVED,QDRANT,STATUS,SETTINGS current
  class CHAT,EVAL,RESEARCH,FUTURE later
```

## Key boundary

Distill creates the Learning Guide from the pinned selection. RAG indexing runs afterward over the complete normalized Ingestion book, including pages excluded from the guide. The index prepares for later retrieval and chat; it does not currently help write the guide.

Search connections can be configured and tested in Settings, but Distill does not call them yet. Add web research after evaluating the source-only guide, and keep external findings separate from what the book says.
