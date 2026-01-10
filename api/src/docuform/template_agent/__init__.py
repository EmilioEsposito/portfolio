"""
Template Field Detection Agent

An AI agent that helps attorneys convert raw DOCX documents into templates
with fillable fields.
"""
from api.src.docuform.template_agent.agent import agent, TemplateAgentContext, FieldToCreate
from api.src.docuform.template_agent.routes import router

__all__ = ["agent", "TemplateAgentContext", "FieldToCreate", "router"]
