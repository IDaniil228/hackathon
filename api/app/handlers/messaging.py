from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import Input, found, record
from app.db.enums import InvitationStatus, UserRole
from app.db.models.core import User
from app.db.models.hire import Employer, Invitation
from app.db.models.messaging import Conversation, Message
from app.db.session import get_session
from app.schemas import InvitationOut
from app.security import current_user, now, require_role

router = APIRouter(tags=["Переписка кандидата и работодателя"])


class MessageInput(Input):
    content: str = Field(min_length=1, max_length=5000)
    client_message_id: UUID


class ReadInput(Input):
    up_to_message_id: int | None = Field(default=None, ge=0)


class MessageOut(BaseModel):
    id: int
    conversation_id: UUID
    sender_user_id: UUID
    client_message_id: UUID | None
    content: str
    kind: Literal["offer", "text"]
    created_at: datetime


class MessagesOut(BaseModel):
    results: list[MessageOut]
    next_cursor: int
    has_more: bool


class ConversationOut(BaseModel):
    id: UUID
    invitation_id: UUID
    employer_user_id: UUID
    candidate_id: UUID
    company_id: UUID
    offer: InvitationOut
    unread_count: int
    last_read_id: int
    peer_read_id: int
    last_message_id: int
    can_send: bool
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConversationsOut(BaseModel):
    total: int
    results: list[ConversationOut]


async def check_participant(session, user, conversation):
    if user.id == conversation.candidate_id:
        await require_role(session, user, UserRole.CANDIDATE)
    elif user.id == conversation.employer_user_id:
        await require_role(session, user, UserRole.EMPLOYER)
        employer = await session.get(Employer, user.id)
        if employer is None or employer.company_id != conversation.company_id:
            raise HTTPException(404, "Диалог не найден")
    else:
        raise HTTPException(404, "Диалог не найден")


async def owned(session, user, conversation_id, lock=False):
    conversation = await found(session, Conversation, conversation_id, lock=lock)
    await check_participant(session, user, conversation)
    return conversation, await found(session, Invitation, conversation.invitation_id, lock=lock)


def send_allowed(conversation, invitation):
    return (
        conversation.closed_at is None
        and invitation.status
        in (InvitationStatus.SENT, InvitationStatus.VIEWED, InvitationStatus.ACCEPTED)
        and (invitation.expires_at is None or invitation.expires_at > now())
    )


def message_out(message, invitation):
    return {
        **record(message, ["is_offer"]),
        "content": invitation.message if message.is_offer else message.content,
        "kind": "offer" if message.is_offer else "text",
    }


async def view(session, user, conversation, invitation):
    mine = (
        conversation.candidate_read_id
        if user.id == conversation.candidate_id
        else conversation.employer_read_id
    )
    peer = (
        conversation.employer_read_id
        if user.id == conversation.candidate_id
        else conversation.candidate_read_id
    )
    unread = await session.scalar(
        select(func.count())
        .select_from(Message)
        .where(
            Message.conversation_id == conversation.id,
            Message.id > mine,
            Message.sender_user_id != user.id,
        )
    )
    last = await session.scalar(
        select(func.max(Message.id)).where(Message.conversation_id == conversation.id)
    )
    return {
        **record(conversation, ["candidate_read_id", "employer_read_id"]),
        "offer": record(invitation),
        "last_read_id": mine,
        "peer_read_id": peer,
        "unread_count": unread,
        "last_message_id": last or 0,
        "can_send": send_allowed(conversation, invitation),
    }


@router.post(
    "/invitations/{invitation_id}/conversation",
    status_code=201,
    response_model=ConversationOut,
    summary="Открыть переписку по отправленному предложению без ожидания принятия",
)
async def open_conversation(
    invitation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    invitation = await found(session, Invitation, invitation_id, lock=True)
    if invitation.sender_user_id is None or invitation.sender_user_id == invitation.candidate_id:
        raise HTTPException(409, "У приглашения нет допустимого собеседника")
    existing = await session.scalar(
        select(Conversation).where(Conversation.invitation_id == invitation_id)
    )
    if existing is not None:
        await check_participant(session, user, existing)
        return await view(session, user, existing, invitation)
    if user.id not in (invitation.candidate_id, invitation.sender_user_id):
        raise HTTPException(404, "Приглашение не найдено")
    if invitation.status not in (
        InvitationStatus.SENT,
        InvitationStatus.VIEWED,
        InvitationStatus.ACCEPTED,
    ) or (invitation.expires_at and invitation.expires_at <= now()):
        raise HTTPException(409, "Предложение закрыто или истекло")
    conversation = Conversation(
        invitation_id=invitation_id,
        employer_user_id=invitation.sender_user_id,
        candidate_id=invitation.candidate_id,
        company_id=invitation.company_id,
    )
    await check_participant(session, user, conversation)
    session.add(conversation)
    await session.flush()
    session.add(
        Message(
            conversation_id=conversation.id,
            sender_user_id=invitation.sender_user_id,
            is_offer=True,
            content=None,
            client_message_id=None,
            created_at=invitation.sent_at,
        )
    )
    await session.commit()
    return await view(session, user, conversation, invitation)


@router.get(
    "/conversations",
    response_model=ConversationsOut,
    summary="Собственные диалоги и непрочитанные сообщения",
)
async def conversations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(
            select(Conversation)
            .where(
                or_(Conversation.candidate_id == user.id, Conversation.employer_user_id == user.id)
            )
            .order_by(Conversation.updated_at.desc(), Conversation.id)
        )
    ).all()
    results = []
    for row in rows:
        try:
            await check_participant(session, user, row)
        except HTTPException:
            continue
        results.append(
            await view(session, user, row, await found(session, Invitation, row.invitation_id))
        )
    return {"total": len(results), "results": results[offset : offset + limit]}


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationOut,
    summary="Предложение, участники и состояние собственного диалога",
)
async def conversation(
    conversation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row, invitation = await owned(session, user, conversation_id)
    return await view(session, user, row, invitation)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessagesOut,
    summary="Сообщения собственного диалога после курсора",
)
async def messages(
    conversation_id: UUID,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row, invitation = await owned(session, user, conversation_id)
    found_messages = (
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == row.id, Message.id > after_id)
            .order_by(Message.id)
            .limit(limit + 1)
        )
    ).all()
    page = found_messages[:limit]
    return {
        "results": [message_out(item, invitation) for item in page],
        "next_cursor": page[-1].id if page else after_id,
        "has_more": len(found_messages) > limit,
    }


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=201,
    response_model=MessageOut,
    summary="Отправить сообщение от текущего участника с защитой повторной отправки",
)
async def send(
    conversation_id: UUID,
    body: MessageInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row, invitation = await owned(session, user, conversation_id, lock=True)
    existing = await session.scalar(
        select(Message).where(
            Message.conversation_id == row.id,
            Message.sender_user_id == user.id,
            Message.client_message_id == body.client_message_id,
        )
    )
    if existing:
        if existing.content != body.content:
            raise HTTPException(409, "Идентификатор сообщения уже использован для другого текста")
        return message_out(existing, invitation)
    if not send_allowed(row, invitation):
        raise HTTPException(409, "Диалог или предложение закрыто")
    message = Message(
        conversation_id=row.id,
        sender_user_id=user.id,
        content=body.content,
        client_message_id=body.client_message_id,
        is_offer=False,
    )
    session.add(message)
    row.updated_at = now()
    await session.commit()
    return message_out(message, invitation)


@router.post(
    "/conversations/{conversation_id}/read",
    response_model=ConversationOut,
    summary="Подтвердить прочтение до сообщения собственного диалога",
)
async def read(
    conversation_id: UUID,
    body: ReadInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row, invitation = await owned(session, user, conversation_id, lock=True)
    target = body.up_to_message_id
    if target is None:
        target = (
            await session.scalar(
                select(func.max(Message.id)).where(Message.conversation_id == row.id)
            )
            or 0
        )
    elif target:
        message = await found(session, Message, target)
        if message.conversation_id != row.id:
            raise HTTPException(422, "Сообщение принадлежит другому диалогу")
    field = "candidate_read_id" if user.id == row.candidate_id else "employer_read_id"
    setattr(row, field, max(getattr(row, field), target))
    await session.commit()
    return await view(session, user, row, invitation)


@router.post(
    "/conversations/{conversation_id}/close",
    response_model=ConversationOut,
    summary="Закрыть собственный диалог для новых сообщений",
)
async def close(
    conversation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row, invitation = await owned(session, user, conversation_id, lock=True)
    row.closed_at = row.closed_at or now()
    await session.commit()
    return await view(session, user, row, invitation)
