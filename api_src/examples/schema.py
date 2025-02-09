from typing import List, Optional
import strawberry
from datetime import datetime
from sqlalchemy import select
from api_src.database import get_session
from api_src.examples.models import Example as ExampleModel

@strawberry.type
class Example:
    id: int
    title: str
    content: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, model: ExampleModel) -> "Example":
        return cls(
            id=model.id,
            title=model.title,
            content=model.content,
            created_at=model.created_at,
            updated_at=model.updated_at
        )

@strawberry.type
class Query:
    @strawberry.field
    async def examples(self) -> List[Example]:
        async with get_session() as session:
            result = await session.execute(select(ExampleModel))
            examples = result.scalars().all()
            return [Example.from_model(ex) for ex in examples]
    
    @strawberry.field
    async def example(self, id: int) -> Optional[Example]:
        async with get_session() as session:
            result = await session.execute(select(ExampleModel).filter(ExampleModel.id == id))
            example = result.scalar_one_or_none()
            return Example.from_model(example) if example else None

@strawberry.type
class Mutation:
    @strawberry.mutation
    async def create_example(self, title: str, content: str) -> Example:
        async with get_session() as session:
            example = ExampleModel(title=title, content=content)
            session.add(example)
            await session.commit()
            await session.refresh(example)
            return Example.from_model(example)
    
    @strawberry.mutation
    async def update_example(self, id: int, title: str, content: str) -> Optional[Example]:
        async with get_session() as session:
            example = await session.get(ExampleModel, id)
            if not example:
                return None
            example.title = title
            example.content = content
            await session.commit()
            await session.refresh(example)
            return Example.from_model(example)
    
    @strawberry.mutation
    async def delete_example(self, id: int) -> bool:
        async with get_session() as session:
            example = await session.get(ExampleModel, id)
            if not example:
                return False
            await session.delete(example)
            await session.commit()
            return True

