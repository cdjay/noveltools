"""Explicit, version-bound decisions emitted by existing canvas review nodes."""
import copy
import json
import re
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .config import settings
from .services import content_sha256

CONTROL_NODE = 'continuous-review-control'
DIMENSIONS = {'plot', 'agency', 'reward', 'reactions', 'readability', 'continuity', 'outline'}
OPEN, CLOSE = '<continuous_decision>', '</continuous_decision>'


class Issue(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(min_length=1, max_length=80)
    certainty: Literal['confirmed', 'optional', 'needs_author']
    blocking: bool
    auto_fixable: bool
    evidence: str = Field(min_length=1, max_length=4000)
    description: str = Field(min_length=1, max_length=2000)


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal[1]
    stage: Literal['review', 'check']
    source_version_id: int
    action: Literal['continue', 'revise', 'review', 'pause']
    reason: str = Field(min_length=1, max_length=2000)
    needs_author: bool
    impact: Literal['none', 'wording', 'story']
    checked_dimensions: list[str]
    issues: list[Issue] = Field(max_length=100)
    resolved_ids: list[str] = Field(max_length=100)


def readable_report(text):
    return text.split(OPEN, 1)[0].rstrip()


def parse_decision(text, stage, version):
    if text.count(OPEN) != 1 or text.count(CLOSE) != 1 or not text.rstrip().endswith(CLOSE):
        raise ValueError('审核没有返回唯一、完整的自动判断结论，请人工查看意见；不会默认通过。')
    raw = text.split(OPEN, 1)[1].split(CLOSE, 1)[0].strip()
    try:
        decision = Decision.model_validate_json(raw)
    except ValueError as exc:
        raise ValueError('自动判断结论格式不完整，请人工查看意见；不会默认通过。') from exc
    if decision.stage != stage or decision.source_version_id != version:
        raise ValueError('自动判断的正文版本或审核阶段不匹配，请人工核对。')
    if len({i.id for i in decision.issues}) != len(decision.issues):
        raise ValueError('审核问题编号重复，请人工核对。')
    if stage == 'review' and not DIMENSIONS.issubset(decision.checked_dimensions):
        raise ValueError('审核未覆盖剧情、行动、回报、反应、流畅度、连续性和章纲，请人工核对。')
    if stage == 'review' and decision.action == 'review' or stage == 'check' and decision.action == 'revise':
        raise ValueError('审核结论与当前阶段不相容，请人工核对。')
    if decision.action == 'continue' and (decision.needs_author or any(i.blocking or i.certainty in ('confirmed', 'needs_author') for i in decision.issues)):
        raise ValueError('审核一面要求处理问题、一面建议继续，结论冲突，已暂停。')
    if decision.action == 'revise' and not any(i.certainty == 'confirmed' and i.auto_fixable for i in decision.issues):
        raise ValueError('审核未提供有依据且可局部修复的确定问题，不能自动改稿。')
    return decision


def decision_prompt(stage, version):
    sample = {'version': 1, 'stage': stage, 'source_version_id': version, 'action': 'pause',
              'reason': '具体理由', 'needs_author': False, 'impact': 'none',
              'checked_dimensions': sorted(DIMENSIONS), 'issues': [], 'resolved_ids': []}
    return '''【本次连续任务的自动判断协议】
作者已授权本次任务按已确认章纲推进，仅对有正文证据的确定问题作必要局部修订。大纲与章纲只读，可选建议不自动采纳，未裁定Q项和需改纲/扩大范围的问题交作者。机器完成不代表正式稿。
本协议只增加最终主编意见/修订复核报告的机器结论，不改变原有分审、正文生成或局部JSON编辑协议。分审节点仍按原规则输出，只有最终主编/复核节点在完整人类可读报告末尾追加一个标签块，块后不再输出文本。
不要仅根据算术正确或已有一项收益判定章节合格。核对剧情推进(plot)、主角主动行动(agency)、实际回报(reward)、人物反应(reactions)、阅读流畅度(readability)、连续性(continuity)、大纲与本章目标(outline)。所有确定问题须能定位正文证据。不是要每章强行找到问题。
review阶段：无确定问题且无阻碍可continue；有证据、范围内可局部修复的确定问题用revise；有争议/需要作者裁定/需改计划用pause。可选建议本身不阻断。不得给出review动作。
check阶段：回读合成后的正文，逐项核对本轮明确采纳的问题是否解决、是否越界或引入新问题。resolved_ids只列本次确已解决的问题编号。issues只列未解决或新增问题。确认解决且无新增问题、修改只涉及错字标点局部表达时continue；涉及因果、人物动机、时间线、能力或章纲落实时impact=story并建议review；仍有可复核问题用review；需要作者决定用pause。不得直接给出revise动作。无法判断不能默认continue。
每个issues对象必须包含id、certainty(confirmed/optional/needs_author)、blocking、auto_fixable、evidence(正文原句及定位)、description。编号须与人类可读意见对应。certainty=needs_author或无法局部处理时不能auto_fixable。impact记录实际修改影响，不能以字数少代替判断。
以下是格式示例而非结论，全部字段必填；stage和source_version_id必须原样填写，其余由证据判断：
''' + OPEN + json.dumps(sample, ensure_ascii=False) + CLOSE


async def ensure_control(canvas, response, target_id):
    """Add one empty opt-in input, retaining original graph and manual behavior."""
    graph = response.json()
    nodes = {n['id']: n for n in graph['nodes']}
    target = nodes.get(target_id)
    if not target or target.get('enabled') is False or target['type'] != 'llm':
        raise HTTPException(409, f'动态审核需要启用的 {target_id} 语言模型节点。')
    edge_id = f'{CONTROL_NODE}-{target_id}'
    if CONTROL_NODE in nodes:
        control = nodes[CONTROL_NODE]
        edge = next((e for e in graph['edges'] if e['id'] == edge_id), None)
        prompt = target['inputs'].get('prompt')
        referenced = isinstance(prompt, dict) and prompt.get('kind') == 'prompt-template' and any(p.get('edgeId') == edge_id for p in prompt.get('parts', []))
        if (control['type'] != 'text' or control.get('enabled') is False or
                any(e['targetNodeId'] == CONTROL_NODE for e in graph['edges']) or not edge or
                edge['sourceNodeId'] != CONTROL_NODE or edge['targetNodeId'] != target_id or
                edge['sourceHandle'] != 'text' or edge['targetHandle'] != 'prompt' or not referenced):
            raise HTTPException(409, '连续审核判断输入或连线已变化，请修复工作流后继续。')
        return response
    changed = copy.deepcopy(graph)
    position = target.get('position', {'x': 0, 'y': 0})
    changed['nodes'].append({'id': CONTROL_NODE, 'type': 'text', 'version': 1,
        'label': '连续审核判断（运行时传入）', 'position': {'x': position['x'] - 380, 'y': position['y'] + 300},
        'inputs': {'text': ''}, 'config': {}})
    changed['edges'].append({'id': edge_id, 'sourceNodeId': CONTROL_NODE, 'sourceHandle': 'text',
                            'targetNodeId': target_id, 'targetHandle': 'prompt'})
    changed_target = next(n for n in changed['nodes'] if n['id'] == target_id)
    prompt = changed_target['inputs'].get('prompt')
    if isinstance(prompt, dict):
        if prompt.get('kind') != 'prompt-template':
            raise HTTPException(409, '当前审核节点提示词格式不支持自动判断。')
        prompt['parts'].extend([{'type': 'text', 'text': '\n\n'}, {'type': 'reference', 'edgeId': edge_id}])
    else:
        parts = []
        # Preserve the effective legacy system/prompt sources before composing the extra input.
        for key in ('system', 'prompt'):
            edge = next((e for e in graph['edges'] if e['targetNodeId'] == target_id and e['targetHandle'] == key), None)
            value = target['inputs'].get(key)
            addition = [{'type': 'reference', 'edgeId': edge['id']}] if edge else [{'type': 'text', 'text': value}] if isinstance(value, str) and value else []
            if parts and addition:
                parts.append({'type': 'text', 'text': '\n\n'})
            parts.extend(addition)
        parts.extend([{'type': 'text', 'text': '\n\n'}, {'type': 'reference', 'edgeId': edge_id}])
        changed_target['inputs'].pop('system', None)
        changed_target['inputs']['prompt'] = {'kind': 'prompt-template', 'parts': parts}
        for edge in changed['edges']:
            if edge['targetNodeId'] == target_id and edge['targetHandle'] == 'system':
                edge['targetHandle'] = 'prompt'
    # Persist the exact pre-change graph before changing the saved canvas.
    directory = Path(settings.data_dir) / 'workflow-backups'
    directory.mkdir(parents=True, exist_ok=True)
    original = json.dumps(graph, ensure_ascii=False, indent=2)
    (directory / f'{content_sha256(original)}.json').write_text(original, encoding='utf-8')
    return await canvas('PUT', f"/workflows/{graph['id']}", headers={'If-Match': response.headers['etag']}, json=changed)
