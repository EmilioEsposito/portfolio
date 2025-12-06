"""
Swagger/OpenAPI schema utilities for FastAPI compatibility
"""
from typing import Any, Dict


def expand_json_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expand JSON schema by inlining all $defs references.
    
    This makes the schema work with FastAPI/Swagger which can't resolve $refs 
    in openapi_extra. The function recursively replaces all $ref references 
    with their actual definitions from the $defs section.
    
    Args:
        schema: JSON schema dictionary that may contain $defs and $ref references
        
    Returns:
        Fully expanded schema dictionary with all $ref references inlined
    """
    if "$defs" not in schema:
        return schema
    
    defs = schema.pop("$defs", {})
    
    def resolve_refs(obj: Any) -> Any:
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_path = obj["$ref"]
                if ref_path.startswith("#/$defs/"):
                    def_name = ref_path.replace("#/$defs/", "")
                    if def_name in defs:
                        # Recursively resolve refs in the definition
                        resolved = resolve_refs(defs[def_name].copy())
                        # Merge any additional properties from the ref object
                        resolved.update({k: v for k, v in obj.items() if k != "$ref"})
                        return resolved
            return {k: resolve_refs(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [resolve_refs(item) for item in obj]
        return obj
    
    return resolve_refs(schema)

