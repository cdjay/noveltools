"""Lightweight relationship graph services inspired by graphify-novel.

The graph is deliberately derived from the local story bible and confirmed
chapter data. It is a query layer, not a second source of truth and never
writes bible entities by itself.
"""

from collections import deque
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Chapter, Character, Event, EventRelation, KnowledgeItem, PlotThread, RelationState


def _key(kind: str, ident: int) -> str:
    return f"{kind}:{ident}"


def _edge(edges: list[dict[str, Any]], seen: set[tuple[str, str, str]], source: str, target: str, relation: str, note: str = "") -> None:
    if source == target:
        return
    signature = (source, target, relation)
    if signature in seen:
        return
    seen.add(signature)
    edges.append({"from": source, "to": target, "relation": relation, "note": note})


def _relationship_items(value: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def build_graph(db: Session, book_id: int) -> dict[str, list[dict[str, Any]]]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    characters = list(db.scalars(select(Character).where(Character.book_id == book_id)))
    knowledge = list(db.scalars(select(KnowledgeItem).where(KnowledgeItem.book_id == book_id)))
    events = list(db.scalars(select(Event).where(Event.book_id == book_id)))
    threads = list(db.scalars(select(PlotThread).where(PlotThread.book_id == book_id)))
    chapters = list(db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)))

    def add(kind: str, ident: int, label: str, description: str = "", **metadata: Any) -> str:
        node_id = _key(kind, ident)
        nodes[node_id] = {"id": node_id, "kind": kind, "label": label, "description": description, **metadata}
        return node_id

    character_ids = {item.id: add("character", item.id, item.name, item.summary or item.identity, status=item.status) for item in characters}
    knowledge_ids = {item.id: add("knowledge", item.id, item.name, item.content, category=item.category, status=item.status) for item in knowledge}
    event_ids = {item.id: add("event", item.id, item.title, item.description, track=item.track, status=item.status,
                              start_chapter=item.start_chapter, end_chapter=item.end_chapter) for item in events}
    thread_ids = {item.id: add("thread", item.id, item.title, item.description, thread_type=item.thread_type, status=item.status,
                               seed_chapter=item.seed_chapter, last_chapter=item.last_chapter) for item in threads}
    chapter_ids = {item.id: add("chapter", item.id, f"第{item.chapter_number}章 {item.title}", "", chapter_number=item.chapter_number) for item in chapters}

    for relation in db.scalars(select(RelationState).where(RelationState.book_id == book_id)):
        source = character_ids.get(relation.from_character_id)
        target = character_ids.get(relation.to_character_id)
        if source and target:
            note = relation.current_note or f"信任 {relation.trust:g}，好感 {relation.liking:g}，敌意 {relation.hostility:g}"
            _edge(edges, seen_edges, source, target, "关系", note)

    for character in characters:
        source = character_ids[character.id]
        for item in _relationship_items(character.relationships):
            name = str(item.get("character") or item.get("target") or "").strip().casefold()
            target = next((character_ids[other.id] for other in characters if other.name.casefold() == name), None)
            if target:
                _edge(edges, seen_edges, source, target, "角色关系", str(item.get("relation") or item.get("change") or ""))

    for relation in db.scalars(select(EventRelation).where(EventRelation.from_event_id.in_(list(event_ids) or [-1]))):
        source = event_ids.get(relation.from_event_id)
        target = event_ids.get(relation.to_event_id)
        if source and target:
            _edge(edges, seen_edges, source, target, relation.relation_type, relation.note)

    for event in events:
        source = event_ids[event.id]
        if event.created_from_chapter_id in chapter_ids:
            _edge(edges, seen_edges, source, chapter_ids[event.created_from_chapter_id], "发生于")
        searchable = f"{event.title} {event.description} {event.objective_fact} {event.hidden_truth}"
        for character in characters:
            if character.name and character.name in searchable:
                _edge(edges, seen_edges, source, character_ids[character.id], "涉及角色")
        for thread in threads:
            if thread.title and thread.title in searchable:
                _edge(edges, seen_edges, source, thread_ids[thread.id], "属于情节线")

    for thread in threads:
        source = thread_ids[thread.id]
        for chapter in chapters:
            if chapter.chapter_number in {thread.seed_chapter, thread.last_chapter}:
                _edge(edges, seen_edges, source, chapter_ids[chapter.id], "涉及章节")

    for chapter in chapters:
        content = ""
        try:
            from .services import serialize_chapter
            content = serialize_chapter(chapter, db).get("current_content", "")
        except Exception:
            content = ""
        for character in characters:
            if character.name and character.name in content:
                _edge(edges, seen_edges, chapter_ids[chapter.id], character_ids[character.id], "章节出现")
        for item in knowledge:
            if item.name and item.name in content:
                _edge(edges, seen_edges, chapter_ids[chapter.id], knowledge_ids[item.id], "章节引用")

    return {"nodes": list(nodes.values()), "edges": edges}


def _adjacency(graph: dict[str, list[dict[str, Any]]]) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    result: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for edge in graph["edges"]:
        result.setdefault(edge["from"], []).append((edge["to"], edge))
        result.setdefault(edge["to"], []).append((edge["from"], {**edge, "from": edge["to"], "to": edge["from"]}))
    return result


def _query_terms(question: str) -> list[str]:
    value = re.sub(r"[？?，。,.！!：:；;、\s]+", " ", question.strip())
    terms = [term for term in value.split(" ") if len(term) >= 2]
    return list(dict.fromkeys([question.strip(), *terms]))


def graph_query(db: Session, book_id: int, question: str, dfs: bool = False) -> dict[str, Any]:
    graph = build_graph(db, book_id)
    terms = _query_terms(question)
    matched = [node for node in graph["nodes"] if any(term and (term.casefold() in node["label"].casefold() or term.casefold() in node["description"].casefold()) for term in terms)]
    matched_ids = {node["id"] for node in matched}
    adjacency = _adjacency(graph)
    selected_ids = set(matched_ids)
    frontier = deque((node_id, 0) for node_id in matched_ids)
    max_depth = 3 if dfs else 1
    while frontier:
        node_id, depth = frontier.popleft()
        if depth >= max_depth:
            continue
        for neighbor, _ in adjacency.get(node_id, []):
            if neighbor not in selected_ids:
                selected_ids.add(neighbor)
                frontier.append((neighbor, depth + 1))
    selected_edges = [edge for edge in graph["edges"] if edge["from"] in selected_ids and edge["to"] in selected_ids]
    return {"question": question, "dfs": dfs, "matched": matched, "nodes": [node for node in graph["nodes"] if node["id"] in selected_ids], "edges": selected_edges}


def graph_path(db: Session, book_id: int, from_name: str, to_name: str) -> dict[str, Any]:
    graph = build_graph(db, book_id)
    def find(name: str) -> str | None:
        exact = next((node["id"] for node in graph["nodes"] if node["label"].casefold() == name.casefold()), None)
        return exact or next((node["id"] for node in graph["nodes"] if name.casefold() in node["label"].casefold()), None)
    source, target = find(from_name), find(to_name)
    if not source or not target:
        return {"from_name": from_name, "to_name": to_name, "found": False, "path": [], "edges": [], "message": "没有找到起点或终点。"}
    adjacency = _adjacency(graph)
    queue = deque([source])
    previous: dict[str, tuple[str | None, dict[str, Any] | None]] = {source: (None, None)}
    while queue:
        current = queue.popleft()
        if current == target:
            break
        for neighbor, edge in adjacency.get(current, []):
            if neighbor not in previous:
                previous[neighbor] = (current, edge)
                queue.append(neighbor)
    if target not in previous:
        return {"from_name": from_name, "to_name": to_name, "found": False, "path": [], "edges": [], "message": "两个对象之间没有已知连接。"}
    node_map = {node["id"]: node for node in graph["nodes"]}
    ids: list[str] = []
    path_edges: list[dict[str, Any]] = []
    current = target
    while current is not None:
        ids.append(current)
        parent, edge = previous[current]
        if edge:
            path_edges.append(edge)
        current = parent
    ids.reverse()
    path_edges.reverse()
    return {"from_name": from_name, "to_name": to_name, "found": True, "path": [node_map[item] for item in ids], "edges": path_edges, "message": "已找到连接路径。"}


def graph_status(db: Session, book_id: int) -> dict[str, Any]:
    threads = list(db.scalars(select(PlotThread).where(PlotThread.book_id == book_id).order_by(PlotThread.updated_at.desc())))
    characters = list(db.scalars(select(Character).where(Character.book_id == book_id).order_by(Character.name)))
    events = list(db.scalars(select(Event).where(Event.book_id == book_id).order_by(Event.start_chapter.nulls_last(), Event.id)))
    open_threads = [item for item in threads if item.status not in {"resolved", "abandoned"}]
    unresolved_events = [item for item in events if item.status in {"ongoing", "进行中", "interrupted", "中断", "unresolved", "未解决", "planned", "计划"}]
    foreshadowing = [item for item in events if item.track in {"foreshadow", "伏笔"} and item.status not in {"revealed", "已揭示", "invalidated", "作废"}]
    graph = build_graph(db, book_id)
    degree: dict[str, int] = {node["id"]: 0 for node in graph["nodes"]}
    for edge in graph["edges"]:
        degree[edge["from"]] = degree.get(edge["from"], 0) + 1
        degree[edge["to"]] = degree.get(edge["to"], 0) + 1
    hubs = sorted((node for node in graph["nodes"] if degree.get(node["id"], 0)), key=lambda node: degree[node["id"]], reverse=True)[:10]
    return {"open_threads": threads_to_dict(open_threads), "character_states": [{"name": item.name, "status": item.status, "current_state": item.current_state, "goals": item.goals} for item in characters],
            "unresolved_events": [event_to_dict(item) for item in unresolved_events], "unresolved_foreshadowing": [event_to_dict(item) for item in foreshadowing],
            "hubs": [{**node, "degree": degree[node["id"]]} for node in hubs], "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"])}


def threads_to_dict(items: list[PlotThread]) -> list[dict[str, Any]]:
    return [{column.name: getattr(item, column.name) for column in PlotThread.__table__.columns} for item in items]


def event_to_dict(item: Event) -> dict[str, Any]:
    return {column.name: getattr(item, column.name) for column in Event.__table__.columns}
