"""Pure compilation-template normalization shared by routes and background workers."""


def compilation_template_kind(kind) -> str:
    if not isinstance(kind, str):
        return ""
    normalized = kind.strip().lower().replace("-", "_")
    if normalized in {"pageindex", "page_index", "knowledge_graph"}:
        return "timeline"
    return normalized
