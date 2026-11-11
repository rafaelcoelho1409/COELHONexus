ROTATOR_TASK = "dd_ingestion_page_repair"
# Calls one repair run may make: the patches, a check of each, and the answer. A run that has not answered by then ends.
MODEL_CALLS = 5
# `check_patch` calls one repair run may make; a refused call goes back to the model as a message and the run goes on.
CHECK_CALLS = 5
