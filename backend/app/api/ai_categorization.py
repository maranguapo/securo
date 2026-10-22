"""The AI categorization review screen's API.

Reads are open to any member; changing a category is a write like any other,
so it takes the same gate as the transactions API.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.ai_categorization import (
    AiItemsResponse,
    AiSummaryRead,
    BulkResultRead,
    CategoryRequest,
    IdsRequest,
    RuleRequest,
    RuleResultRead,
)
from app.services import ai_review_service
from app.services.rule_service import DuplicateRuleError

router = APIRouter(prefix="/api/ai-categorization", tags=["ai-categorization"])


@router.get("/summary", response_model=AiSummaryRead)
async def get_summary(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await ai_review_service.summary(session, ctx.workspace.id)


@router.get("/items", response_model=AiItemsResponse)
async def list_items(
    tab: str = Query("review", pattern="^(review|applied)$"),
    status: Optional[str] = Query(None, pattern="^(applied|approved|changed)$"),
    min_confidence: Optional[float] = Query(None, ge=0, le=1),
    max_confidence: Optional[float] = Query(None, ge=0, le=1),
    q: Optional[str] = Query(None, max_length=100),
    limit: int = Query(ai_review_service.MAX_ITEMS, ge=1, le=ai_review_service.MAX_ITEMS),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    items, more = await ai_review_service.list_items(
        session,
        ctx.workspace.id,
        tab=tab,
        status=status,
        min_confidence=min_confidence,
        max_confidence=max_confidence,
        q=q,
        limit=limit,
    )
    return {"items": items, "has_more": more}


@router.post("/items/approve", response_model=BulkResultRead)
async def approve(
    body: IdsRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    result = await ai_review_service.approve_many(session, ctx.workspace.id, ctx.user_id, body.ids)
    return {"done": result.done, "skipped": result.skipped}


@router.post("/items/category", response_model=BulkResultRead)
async def set_category(
    body: CategoryRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    result = await ai_review_service.set_category_many(
        session, ctx.workspace.id, ctx.user_id, body.ids, body.category_id
    )
    if result.done == 0 and result.skipped:
        raise HTTPException(status_code=404, detail="Items or category not found")
    return {"done": result.done, "skipped": result.skipped}


@router.post("/items/undo", response_model=BulkResultRead)
async def undo(
    body: IdsRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    result = await ai_review_service.undo_many(session, ctx.workspace.id, ctx.user_id, body.ids)
    return {"done": result.done, "skipped": result.skipped}


@router.post("/items/rule", response_model=RuleResultRead)
async def create_rule(
    body: RuleRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        result = await ai_review_service.rule_from_item(
            session, ctx.workspace.id, ctx.user_id, body.id, body.category_id
        )
    except ai_review_service.MerchantRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except DuplicateRuleError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"rule_id": result.rule_id, "rule_name": result.rule_name, "applied": result.applied}
