class IndexRebuildRequired(Exception):
    """The job has no usable persisted retrieval index."""


INDEX_REBUILD_MESSAGE = "This video's search index is unavailable. Submit the video again to rebuild it."
