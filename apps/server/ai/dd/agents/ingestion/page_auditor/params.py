ROTATOR_TASK = "dd_ingestion_page_auditor"
# The auditor answers in one call; a few more cover a retried structured answer. A run that has not answered by then ends.
MODEL_CALLS = 3
# Where the prompt lists the export conventions, rendered from the table the audit filter uses.
CONVENTIONS_MARKER = "<<EXPORT_CONVENTIONS>>"
