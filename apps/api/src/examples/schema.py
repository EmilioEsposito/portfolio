from typing import List, Optional
import strawberry
from datetime import datetime
from sqlalchemy import select, desc
from apps.api.src.database.database import session_context
from apps.api.src.examples.models import Example as ExampleModel


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
class ExampleError:
    message: str

@strawberry.type
class ExampleResponse:
    example: Optional[Example] = None
    error: Optional[ExampleError] = None

@strawberry.type
class ExamplesResponse:
    examples: List[Example]
    error: Optional[ExampleError] = None

@strawberry.type
class Query:
    @strawberry.field
    async def examples(self) -> List[Example]:
        async with session_context() as session:
            result = await session.execute(
                select(ExampleModel).order_by(desc(ExampleModel.created_at))
            )
            return result.scalars().all()
    
    @strawberry.field
    async def example(self, id: int) -> Optional[Example]:
        async with session_context() as session:
            result = await session.execute(
                select(ExampleModel).filter(ExampleModel.id == id)
            )
            return result.scalar_one_or_none()

@strawberry.type
class Mutation:
    @strawberry.mutation
    async def create_example(self, title: str, content: str) -> Example:
        async with session_context() as session:
            example = ExampleModel(title=title, content=content)
            session.add(example)
            await session.commit()
            await session.refresh(example)
            return example
    
    @strawberry.mutation
    async def update_example(
        self, id: int, title: Optional[str] = None, content: Optional[str] = None
    ) -> Optional[Example]:
        async with session_context() as session:
            example = await session.get(ExampleModel, id)
            if not example:
                return None
            
            if title is not None:
                example.title = title
            if content is not None:
                example.content = content
            
            await session.commit()
            await session.refresh(example)
            return example
    
    @strawberry.mutation
    async def delete_example(self, id: int) -> bool:
        async with session_context() as session:
            example = await session.get(ExampleModel, id)
            if not example:
                return False
            
            await session.delete(example)
            await session.commit()
            return True

