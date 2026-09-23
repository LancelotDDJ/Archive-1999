# -*- coding: utf-8 -*-
"""问答与推演路由：NDJSON 流式。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ...schemas import AskBody, SimBody
from ...services import agent_service, qa_service, story_service
from ..deps import rate_limited_user

router = APIRouter(prefix="/qa", tags=["qa"])

_NDJSON_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _ndjson(events) -> StreamingResponse:
    def gen():
        for ev in events:
            yield json.dumps(ev, ensure_ascii=False) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers=_NDJSON_HEADERS)


@router.post("/ask")
def ask(body: AskBody, user: dict = Depends(rate_limited_user)):
    return _ndjson(qa_service.ask_events(
        body, user, agent_runner=agent_service.run_agent_stream))


@router.post("/simulate")
def simulate(body: SimBody, user: dict = Depends(rate_limited_user)):
    from ...core.retrieval.engine import RetrievalEngine
    return _ndjson(story_service.run_simulation_stream(
        body, user, RetrievalEngine.get()))
