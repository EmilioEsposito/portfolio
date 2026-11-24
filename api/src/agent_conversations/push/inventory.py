"""Read-only push registration inventory for the team administrator."""

from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.agent_conversations.push.models import WebPushSubscription
from api.src.agent_conversations.push.service import is_ios_app_subscription
from api.src.utils.clerk import clerk_client


class Registration(BaseModel):
    id: int
    ios_home_screen: bool
    user_agent: str | None
    created_at: datetime
    updated_at: datetime


class MemberRegistrations(BaseModel):
    email: str
    registrations: list[Registration] = Field(default_factory=list)


async def team_registrations(session: AsyncSession) -> list[MemberRegistrations]:
    members: dict[str, MemberRegistrations] = {}
    limit = 100
    offset = 0
    while True:
        users = await clerk_client.users.list_async(
            request={"email_address_query": "@serniacapital.com", "limit": limit, "offset": offset}
        )
        for user in users:
            for address in user.email_addresses:
                if (
                    address.email_address.lower().endswith("@serniacapital.com")
                    and address.verification
                    and address.verification.status == "verified"
                ):
                    members[user.id] = MemberRegistrations(email=address.email_address)
                    if address.id == user.primary_email_address_id:
                        break
        if len(users) < limit:
            break
        offset += limit

    sub = WebPushSubscription
    rows = await session.execute(
        select(
            sub.clerk_user_id, sub.id, sub.endpoint, sub.user_agent, sub.created_at, sub.updated_at
        )
        .where(sub.clerk_user_id.in_(members.keys()))
        .order_by(sub.updated_at.desc(), sub.id)
    )
    for row in rows:
        members[row.clerk_user_id].registrations.append(
            Registration(
                id=row.id,
                ios_home_screen=is_ios_app_subscription(row.endpoint, row.user_agent),
                user_agent=row.user_agent,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    return sorted(members.values(), key=lambda member: member.email.lower())
